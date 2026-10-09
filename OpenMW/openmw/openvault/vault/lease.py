"""Single-kid lease for a connector service. Not a query broker.

One lease is one tenant key, one Space, one owner, and a short TTL. It is
single use. The owner is the service_id already stored on the key. Mint and
redeem learn that service from ``verify_service`` on the Bearer. They do not
read it from the body.

Space is derived from the Bearer. A Space credential is the service_id
``dms:<space>`` in lowercase, minted once with ``POST /keys/services`` by
``X-OpenVault-Admin`` on a loopback socket, and reused on every later call.
This module does not mint or refresh that credential. The tenant
is ``owner_tenant`` on the kid, stored by admin ``POST /api/keys/owner``.
A ``space`` or ``tenant`` in the JSON body is not the source of truth. If
either is present and it disagrees with the Bearer-derived Space or the
binding's tenant, mint and redeem refuse, and redeem does not consume the
lease. Credential-bound Space (#165) is this binding.

The ref is ``ovlease_`` plus a random token. Only its SHA-256 is stored. Raw
secret shapes are refused before any lookup. There is no feature flag: a row
is inserted only when tenant_key, ttl_s, and owner_service_id are all set.

A ref placed in the query string is refused and is not redeemed. The process
access log can still record that request line.

A successful redeem writes ``lease_redeem`` before the consume commits. If
that write fails, the lease is not consumed and the secret is not returned.

This module does not open a database connection for the caller and does not
return a path that contains the kid, the ref, or the secret.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import structlog

from openmw.openvault.paths import keys_db_path
from openmw.openvault.vault.crypto import VaultSealedError
from openmw.openvault.vault.store import KeyRecord, KeyVault
from openmw.openvault.vault.trust import space_from_service_id

log = structlog.get_logger()

LEASE_MINT_PATH = "/api/keys/leases"
LEASE_REDEEM_PATH = "/api/keys/leases/redeem"
OWNER_ASSIGN_PATH = "/api/keys/owner"

REF_PREFIX = "ovlease_"
#: ``token_urlsafe(32)`` is 43 unpadded base64url characters.
_REF_BODY_LEN = 43
_LEASE_REF = re.compile(rf"^{REF_PREFIX}[A-Za-z0-9_-]{{{_REF_BODY_LEN}}}\Z")
#: Shapes a raw credential uses. Matched on the whole ref, case-insensitive.
_RAW_PREFIXES = ("akia", "ghp_", "sk-ant", "xoxb")
#: A non-ovlease string at least this long is treated as a raw secret.
_LONG_PLAIN = 20

DEFAULT_LEASE_TTL_S = 60
MAX_LEASE_TTL_S = 120

ERR_NOT_OWNED = "lease_kid_not_owned"
ERR_NOT_TENANT = "lease_not_tenant_key"
ERR_NOT_OWNER = "lease_not_owner"
ERR_EXPIRED = "lease_expired"
ERR_REUSED = "lease_reused"
ERR_RAW = "lease_raw_secret_ref"
ERR_REF_INVALID = "lease_ref_invalid"
ERR_REF_IN_URL = "lease_ref_in_url"
ERR_UNKNOWN = "lease_ref_unknown"
ERR_BAD = "lease_bad_request"
ERR_AUDIT = "lease_audit_failed"
ERR_SPACE_CREDENTIAL = "lease_space_credential"
ERR_TRANSPORT = "lease_transport"
ERR_SEALED = "vault_sealed"
ERR_SPACE_MISMATCH = "lease_space_mismatch"
ERR_SPACE_UNBOUND = "lease_space_unbound"
ERR_TENANT_MISMATCH = "lease_tenant_mismatch"
ERR_GRANT = "lease_grant_incomplete"

_ABSENT_DIGEST = "0" * 64
_KID_MAX = 256
_SPACE_MAX = 128
_TENANT_MAX = 128

# Grant columns that must be set on every row. No flag skips them.
GRANT_FIELDS = ("tenant_key", "ttl_s", "owner_service_id")


class LeaseError(Exception):
    """Named lease refusal. ``code`` is safe to return. It is not a secret."""

    def __init__(
        self,
        code: str,
        status: int,
        *,
        key_id: str = "",
        space: str = "",
        owner_tenant: str = "",
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.key_id = key_id
        self.space = space
        self.owner_tenant = owner_tenant


@dataclass(frozen=True)
class MintedLease:
    """What mint returns once. ``ref`` is not stored."""

    ref: str
    tenant_key: str
    ttl_s: int
    owner_service_id: str
    space: str
    expires_at: int

    def to_dict(self) -> dict[str, str | int]:
        return {
            "ref": self.ref,
            "tenant_key": self.tenant_key,
            "ttl_s": self.ttl_s,
            "owner_service_id": self.owner_service_id,
            "space": self.space,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class RedeemedLease:
    """Plaintext, once. The ref is not repeated."""

    secret: str
    tenant_key: str
    ttl_s: int
    owner_service_id: str
    space: str
    expires_at: int
    owner_tenant: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "secret": self.secret,
            "tenant_key": self.tenant_key,
            "ttl_s": self.ttl_s,
            "owner_service_id": self.owner_service_id,
            "space": self.space,
            "expires_at": self.expires_at,
            "owner_tenant": self.owner_tenant,
        }


def is_raw_secret_ref(value: str) -> bool:
    """True for AKIA, ghp_, sk-ant, xoxb, and long strings that are not ovlease refs."""
    lower = value.lower()
    for prefix in _RAW_PREFIXES:
        if lower.startswith(prefix):
            return True
    if _LEASE_REF.fullmatch(value):
        return False
    return len(value) >= _LONG_PLAIN


def classify_ref(value: object) -> str:
    """``ok``, ``raw``, or ``invalid``. Does not log ``value``."""
    if not isinstance(value, str):
        return "invalid"
    text = value.strip()
    if not text:
        return "invalid"
    if is_raw_secret_ref(text):
        return "raw"
    if _LEASE_REF.fullmatch(text):
        return "ok"
    return "invalid"


def grant_is_complete(tenant_key: str, ttl_s: int, owner_service_id: str) -> bool:
    """True only when the three grant fields are present. A flag cannot skip this."""
    if not tenant_key.strip() or not owner_service_id.strip():
        return False
    return ttl_s > 0


def _bounded(value: str, limit: int) -> bool:
    """True for a single-line token that fits ``limit``."""
    if not value or len(value) > limit:
        return False
    return "\n" not in value and "\r" not in value


def _binding_error(record: KeyRecord, space_id: str, tenant_id: str) -> LeaseError | None:
    """Refuse a Space or tenant that is not the one stored on the kid.

    ``account_id`` is the vault tenant when the key was created under an
    account. It must equal the assigned owner tenant. A kid with a service
    and no Space is ``lease_space_unbound``.
    """
    owner_space = (record.owner_space or "").strip()
    if not owner_space:
        return LeaseError(ERR_SPACE_UNBOUND, 403)
    if owner_space != space_id:
        return LeaseError(ERR_SPACE_MISMATCH, 403)
    owner_tenant = (record.owner_tenant or "").strip()
    account_id = (record.account_id or "").strip()
    if not owner_tenant or owner_tenant != tenant_id:
        return LeaseError(ERR_TENANT_MISMATCH, 403)
    if account_id and account_id != owner_tenant:
        return LeaseError(ERR_TENANT_MISMATCH, 403)
    return None


def _digest(ref: str) -> str:
    return hashlib.sha256(ref.encode("utf-8")).hexdigest()


def _new_ref() -> str:
    return REF_PREFIX + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class _LeaseRow:
    ref_sha256: str
    tenant_key: str
    ttl_s: int
    owner_service_id: str
    space: str
    tenant_id: str
    expires_at: int
    consumed_at: int | None


class LeaseStore:
    """Lease rows in the same ``keys.db`` as the vault. Not a second vault."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path if db_path is not None else keys_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS key_leases (
                  ref_sha256 TEXT PRIMARY KEY,
                  tenant_key TEXT NOT NULL,
                  ttl_s INTEGER NOT NULL,
                  owner_service_id TEXT NOT NULL,
                  space_id TEXT NOT NULL,
                  tenant_id TEXT,
                  expires_at INTEGER NOT NULL,
                  consumed_at INTEGER,
                  created_at INTEGER NOT NULL,
                  CHECK (tenant_key != ''),
                  CHECK (owner_service_id != ''),
                  CHECK (ttl_s > 0),
                  CHECK (space_id != '')
                )
                """
            )
            cols = {row[1] for row in conn.execute("PRAGMA table_info(key_leases)")}
            if "tenant_id" not in cols:
                conn.execute("ALTER TABLE key_leases ADD COLUMN tenant_id TEXT")
            conn.commit()

    def mint(
        self,
        vault: KeyVault,
        *,
        service_id: str,
        kid: str,
        space: str | None,
        tenant: str | None,
        ttl_s: int,
    ) -> MintedLease:
        """Mint one lease. Space comes from the Bearer, not from ``space``.

        ``service_id`` is the verified bearer. ``space`` and ``tenant`` are
        optional body claims. A present claim that disagrees is refused.
        """
        owner = service_id.strip()
        if not owner:
            raise LeaseError(ERR_NOT_OWNED, 403)
        derived = space_from_service_id(owner)
        if not derived:
            log.info("lease_refused", reason=ERR_SPACE_CREDENTIAL)
            raise LeaseError(ERR_SPACE_CREDENTIAL, 403)
        key_id = kid.strip()
        if not key_id or len(key_id) > _KID_MAX:
            raise LeaseError(ERR_BAD, 400)
        if space is not None and space.strip() != derived:
            log.info("lease_refused", reason=ERR_SPACE_MISMATCH)
            raise LeaseError(ERR_SPACE_MISMATCH, 403, space=derived)
        if ttl_s < 1 or ttl_s > MAX_LEASE_TTL_S:
            raise LeaseError(ERR_BAD, 400)
        record = vault.get(key_id)
        if record is None or record.owner_service_id != owner:
            log.info("lease_refused", reason=ERR_NOT_OWNED)
            raise LeaseError(ERR_NOT_OWNED, 403, space=derived)
        if record.custody != "tenant":
            log.info("lease_refused", reason=ERR_NOT_TENANT)
            raise LeaseError(ERR_NOT_TENANT, 403, key_id=key_id, space=derived)
        if record.lifecycle != "active" or not record.enabled:
            log.info("lease_refused", reason=ERR_NOT_OWNED)
            raise LeaseError(ERR_NOT_OWNED, 403, key_id=key_id, space=derived)
        bound_tenant = (record.owner_tenant or "").strip()
        if tenant is not None and tenant.strip() != bound_tenant:
            log.info("lease_refused", reason=ERR_TENANT_MISMATCH)
            raise LeaseError(
                ERR_TENANT_MISMATCH,
                403,
                key_id=key_id,
                space=derived,
                owner_tenant=bound_tenant,
            )
        binding = _binding_error(record, derived, bound_tenant)
        if binding is not None:
            log.info("lease_refused", reason=binding.code)
            raise LeaseError(
                binding.code,
                binding.status,
                key_id=key_id,
                space=derived,
                owner_tenant=bound_tenant,
            )
        space_id = derived
        tenant_id = bound_tenant
        if not grant_is_complete(key_id, ttl_s, owner):
            raise LeaseError(ERR_GRANT, 400)
        now = int(time.time())
        expires_at = now + ttl_s
        ref = _new_ref()
        digest = _digest(ref)
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO key_leases (
                  ref_sha256, tenant_key, ttl_s, owner_service_id, space_id,
                  tenant_id, expires_at, consumed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (digest, key_id, ttl_s, owner, space_id, tenant_id, expires_at, now),
            )
            conn.commit()
        log.info("lease_minted", service_id=owner)
        return MintedLease(
            ref=ref,
            tenant_key=key_id,
            ttl_s=ttl_s,
            owner_service_id=owner,
            space=space_id,
            expires_at=expires_at,
        )

    def redeem(
        self,
        vault: KeyVault,
        *,
        service_id: str,
        ref: str,
        audit: Callable[[str, str, str, int, str], None],
        body_space: str | None = None,
        body_tenant: str | None = None,
    ) -> RedeemedLease:
        """Return the plaintext once. Audit runs before the consume commits.

        ``audit`` is ``(key_id, space, service_id, expires_at, owner_tenant)``.
        If it raises, the consume rolls back and the secret is not read.
        ``body_space`` and ``body_tenant`` are optional claims. A present
        claim that disagrees does not consume the lease.
        """
        owner = service_id.strip()
        derived = space_from_service_id(owner)
        kind = classify_ref(ref)
        if kind == "raw":
            log.info("lease_refused", reason=ERR_RAW)
            raise LeaseError(ERR_RAW, 400, space=derived)
        if kind != "ok":
            log.info("lease_refused", reason=ERR_REF_INVALID)
            raise LeaseError(ERR_REF_INVALID, 400, space=derived)
        if not owner or not derived:
            log.info("lease_refused", reason=ERR_SPACE_CREDENTIAL)
            raise LeaseError(ERR_SPACE_CREDENTIAL, 403, space=derived)
        if body_space is not None and body_space.strip() != derived:
            log.info("lease_refused", reason=ERR_SPACE_MISMATCH)
            raise LeaseError(ERR_SPACE_MISMATCH, 403, space=derived)
        if vault.seal.is_sealed:
            log.info("lease_refused", reason=ERR_SEALED)
            raise LeaseError(ERR_SEALED, 403, space=derived)
        offered = _digest(ref.strip())
        row = self._lookup(offered)
        if row is None:
            log.info("lease_refused", reason=ERR_UNKNOWN)
            raise LeaseError(ERR_UNKNOWN, 404, space=derived)
        if row.owner_service_id != owner or row.space != derived:
            log.info("lease_refused", reason=ERR_NOT_OWNER)
            raise LeaseError(
                ERR_NOT_OWNER,
                403,
                key_id=row.tenant_key,
                space=row.space,
                owner_tenant=row.tenant_id,
            )
        now = int(time.time())
        if row.consumed_at is not None:
            log.info("lease_refused", reason=ERR_REUSED)
            raise LeaseError(
                ERR_REUSED,
                403,
                key_id=row.tenant_key,
                space=row.space,
                owner_tenant=row.tenant_id,
            )
        if row.expires_at <= now:
            log.info("lease_refused", reason=ERR_EXPIRED)
            raise LeaseError(
                ERR_EXPIRED,
                403,
                key_id=row.tenant_key,
                space=row.space,
                owner_tenant=row.tenant_id,
            )
        record = vault.get(row.tenant_key)
        if (
            record is None
            or record.owner_service_id != owner
            or record.custody != "tenant"
            or record.lifecycle != "active"
            or not record.enabled
        ):
            self._consume(offered, now)
            log.info("lease_refused", reason=ERR_NOT_OWNED)
            raise LeaseError(ERR_NOT_OWNED, 403, key_id=row.tenant_key, space=derived)
        bound_tenant = (record.owner_tenant or "").strip()
        if body_tenant is not None and body_tenant.strip() != bound_tenant:
            log.info("lease_refused", reason=ERR_TENANT_MISMATCH)
            raise LeaseError(
                ERR_TENANT_MISMATCH,
                403,
                key_id=row.tenant_key,
                space=derived,
                owner_tenant=bound_tenant,
            )
        binding = _binding_error(record, derived, row.tenant_id)
        if binding is not None:
            log.info("lease_refused", reason=binding.code)
            raise LeaseError(
                binding.code,
                binding.status,
                key_id=row.tenant_key,
                space=derived,
                owner_tenant=bound_tenant,
            )
        self._consume_after_audit(
            offered,
            now,
            lambda: audit(row.tenant_key, derived, owner, row.expires_at, bound_tenant),
            key_id=row.tenant_key,
            space=derived,
            owner_tenant=bound_tenant,
        )
        try:
            secret = vault.get_secret(row.tenant_key)
        except VaultSealedError as exc:
            raise LeaseError(ERR_SEALED, 403, key_id=row.tenant_key, space=derived) from exc
        if secret is None:
            log.info("lease_refused", reason=ERR_NOT_OWNED)
            raise LeaseError(ERR_NOT_OWNED, 403, key_id=row.tenant_key, space=derived)
        log.info("lease_redeemed", service_id=owner)
        return RedeemedLease(
            secret=secret,
            tenant_key=row.tenant_key,
            ttl_s=row.ttl_s,
            owner_service_id=row.owner_service_id,
            space=row.space,
            expires_at=row.expires_at,
            owner_tenant=bound_tenant,
        )

    def _lookup(self, offered: str) -> _LeaseRow | None:
        """Hash lookup plus one digest compare. A miss still compares."""
        with contextlib.closing(self._connect()) as conn, conn:
            found = conn.execute(
                "SELECT * FROM key_leases WHERE ref_sha256 = ?",
                (offered,),
            ).fetchone()
        stored = _ABSENT_DIGEST
        if found is not None:
            stored = str(found["ref_sha256"])
        digest_ok = hmac.compare_digest(offered, stored)
        if not digest_ok or found is None:
            return None
        consumed = found["consumed_at"]
        names = found.keys()
        raw_tenant = found["tenant_id"] if "tenant_id" in names else None
        return _LeaseRow(
            ref_sha256=stored,
            tenant_key=str(found["tenant_key"]),
            ttl_s=int(found["ttl_s"]),
            owner_service_id=str(found["owner_service_id"]),
            space=str(found["space_id"]),
            tenant_id="" if raw_tenant is None else str(raw_tenant),
            expires_at=int(found["expires_at"]),
            consumed_at=None if consumed is None else int(consumed),
        )

    def _consume_after_audit(
        self,
        digest: str,
        now: int,
        audit: Callable[[], None],
        *,
        key_id: str,
        space: str,
        owner_tenant: str,
    ) -> None:
        """Consume one live row only after ``audit`` returns.

        The update is held in a transaction. An audit failure rolls it back,
        so the lease stays redeemable and the caller does not receive the secret.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE key_leases
                SET consumed_at = ?
                WHERE ref_sha256 = ? AND consumed_at IS NULL AND expires_at > ?
                """,
                (now, digest, now),
            )
            if cur.rowcount != 1:
                conn.rollback()
                self._refuse_spent(digest, now)
            try:
                audit()
            except OSError as exc:
                conn.rollback()
                log.info("lease_refused", reason=ERR_AUDIT)
                raise LeaseError(
                    ERR_AUDIT,
                    503,
                    key_id=key_id,
                    space=space,
                    owner_tenant=owner_tenant,
                ) from exc
            conn.commit()
        except LeaseError:
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _consume(self, digest: str, now: int) -> bool:
        """Mark one live row used. False when it was already used or expired."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE key_leases
                SET consumed_at = ?
                WHERE ref_sha256 = ? AND consumed_at IS NULL AND expires_at > ?
                """,
                (now, digest, now),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _refuse_spent(self, digest: str, now: int) -> NoReturn:
        row = self._lookup(digest)
        if row is not None and row.consumed_at is not None:
            log.info("lease_refused", reason=ERR_REUSED)
            raise LeaseError(ERR_REUSED, 403)
        if row is not None and row.expires_at <= now:
            log.info("lease_refused", reason=ERR_EXPIRED)
            raise LeaseError(ERR_EXPIRED, 403)
        log.info("lease_refused", reason=ERR_REUSED)
        raise LeaseError(ERR_REUSED, 403)


__all__ = [
    "DEFAULT_LEASE_TTL_S",
    "ERR_AUDIT",
    "ERR_BAD",
    "ERR_EXPIRED",
    "ERR_GRANT",
    "ERR_NOT_OWNED",
    "ERR_NOT_OWNER",
    "ERR_NOT_TENANT",
    "ERR_RAW",
    "ERR_REF_INVALID",
    "ERR_REF_IN_URL",
    "ERR_REUSED",
    "ERR_SEALED",
    "ERR_SPACE_CREDENTIAL",
    "ERR_SPACE_MISMATCH",
    "ERR_SPACE_UNBOUND",
    "ERR_TENANT_MISMATCH",
    "ERR_TRANSPORT",
    "ERR_UNKNOWN",
    "GRANT_FIELDS",
    "LEASE_MINT_PATH",
    "LEASE_REDEEM_PATH",
    "MAX_LEASE_TTL_S",
    "OWNER_ASSIGN_PATH",
    "REF_PREFIX",
    "LeaseError",
    "LeaseStore",
    "MintedLease",
    "RedeemedLease",
    "classify_ref",
    "grant_is_complete",
    "is_raw_secret_ref",
]
