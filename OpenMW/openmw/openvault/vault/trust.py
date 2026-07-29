"""Trust root and short-lived intermediate signing keys.

Custody is OpenVault's job, so the signing root lives here and nowhere else. A
service that needs to sign something — today, DMS signing session manifests for
the Cortex executor — asks for a short-lived *intermediate* key. It never sees
the root, and the root never leaves this process.

Why a third table in ``keys.db`` rather than a new file: the same argument
``secrets.py`` already makes. One master key, one thing to back up, one thing to
lose. ``ProviderKind`` is a closed set of upstream vendors and an Ed25519
signing key is not one of them, so it gets its own schema — but the same
database and the same :class:`Seal`.

Three properties this module exists to hold:

* **The private half of an intermediate is returned exactly once.** It is
  handed back at issue time and never stored in a form this process can hand
  out again. A key you can re-read off disk is a key that leaks with the disk.
* **Intermediates expire on their own.** The lifetime is carried in the key
  material and published with it, so a consumer that cannot reach OpenVault
  still stops trusting the key on time. Availability of the vault must never
  extend a key's life.
* **The root signs intermediates and nothing else.** It is not published in the
  JWKS, so a manifest signed directly by the root is not accepted by anything.
  Least privilege for the one key that cannot be rotated cheaply.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from openmw.openvault.paths import keys_db_path
from openmw.openvault.vault.crypto import Seal

KeyKind = Literal["root", "intermediate"]
KeyLifecycle = Literal["active", "revoked", "rotated", "expired"]

#: Intermediates are minutes-scale by design. A manifest is signed at the start
#: of a session and verified within it; a signing key that outlives the session
#: is a signing key someone can steal and reuse.
DEFAULT_INTERMEDIATE_TTL_S = 900
MAX_INTERMEDIATE_TTL_S = 3600


class TrustError(RuntimeError):
    """Raised when a trust operation cannot be completed safely."""


def b64u(raw: bytes) -> str:
    """Unpadded base64url — the one encoding on this wire, per RFC 8037."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _raw_private(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def binding_bytes(*, kid: str, subject: str, public_key_b64: str, nbf: int, exp: int) -> bytes:
    """Exactly what the root signs when it vouches for an intermediate.

    Sorted keys, no whitespace — the same canonicalisation rule the manifest
    itself uses, so there is one convention on this wire rather than two.
    Binding the subject and the window into the signature is the point: without
    them the root would be attesting to a public key with no statement about who
    may use it or for how long.
    """
    return json.dumps(
        {"kid": kid, "subject": subject, "x": public_key_b64, "nbf": nbf, "exp": exp},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class SigningKeyRecord:
    """Public view of a signing key. Never carries the private half."""

    kid: str
    kind: KeyKind
    subject: str
    public_key: str
    parent_kid: str | None
    chain_signature: str | None
    not_before: float
    not_after: float | None
    lifecycle: KeyLifecycle
    created_at: float
    revoked_reason: str | None = None

    def is_usable(self, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        if self.lifecycle != "active":
            return False
        if moment < self.not_before:
            return False
        return not (self.not_after is not None and moment >= self.not_after)


@dataclass(frozen=True)
class IssuedIntermediate:
    """What the caller gets back once, and only once."""

    record: SigningKeyRecord
    private_key: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "kid": self.record.kid,
            "subject": self.record.subject,
            "alg": "EdDSA",
            "crv": "Ed25519",
            "private_key": self.private_key,
            "public_key": self.record.public_key,
            "not_before": int(self.record.not_before),
            "not_after": int(self.record.not_after or 0),
            "issuer_kid": self.record.parent_kid,
            "chain_signature": self.record.chain_signature,
        }


class TrustStore:
    """Custody for the signing root and the intermediates it issues."""

    def __init__(self, db_path: Path | None = None, seal: Seal | None = None) -> None:
        self._db_path = db_path or keys_db_path()
        self._seal = seal or Seal()
        self._init_schema()

    # -- plumbing ------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signing_keys (
                  kid TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  subject TEXT NOT NULL DEFAULT '',
                  public_key TEXT NOT NULL,
                  private_blob BLOB,
                  parent_kid TEXT,
                  chain_signature TEXT,
                  not_before REAL NOT NULL,
                  not_after REAL,
                  lifecycle TEXT NOT NULL DEFAULT 'active',
                  created_at REAL NOT NULL,
                  revoked_reason TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signing_services (
                  service_id TEXT PRIMARY KEY,
                  token_sha256 TEXT NOT NULL,
                  created_at REAL NOT NULL,
                  lifecycle TEXT NOT NULL DEFAULT 'active'
                )
                """
            )
            conn.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> SigningKeyRecord:
        return SigningKeyRecord(
            kid=row["kid"],
            kind=row["kind"],
            subject=row["subject"],
            public_key=row["public_key"],
            parent_kid=row["parent_kid"],
            chain_signature=row["chain_signature"],
            not_before=row["not_before"],
            not_after=row["not_after"],
            lifecycle=row["lifecycle"],
            created_at=row["created_at"],
            revoked_reason=row["revoked_reason"],
        )

    # -- root ----------------------------------------------------------------

    def ensure_root(self) -> SigningKeyRecord:
        """Return the trust root, creating it on first use.

        Created lazily rather than at install so a vault that never signs
        anything never holds a signing key at all.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM signing_keys WHERE kind='root' AND lifecycle='active' "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                return self._row(row)

        private = Ed25519PrivateKey.generate()
        public_b64 = b64u(_raw_public(private.public_key()))
        now = time.time()
        record = SigningKeyRecord(
            kid=f"root-{uuid.uuid4().hex[:12]}",
            kind="root",
            subject="openvault-trust-root",
            public_key=public_b64,
            parent_kid=None,
            chain_signature=None,
            not_before=now,
            not_after=None,  # the root does not self-expire; it is rotated
            lifecycle="active",
            created_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO signing_keys (kid, kind, subject, public_key, private_blob, "
                "parent_kid, chain_signature, not_before, not_after, lifecycle, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.kid,
                    record.kind,
                    record.subject,
                    record.public_key,
                    self._seal.encrypt(b64u(_raw_private(private))),
                    None,
                    None,
                    record.not_before,
                    None,
                    record.lifecycle,
                    record.created_at,
                ),
            )
            conn.commit()
        return record

    def _root_private(self) -> Ed25519PrivateKey:
        root = self.ensure_root()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT private_blob FROM signing_keys WHERE kid=?", (root.kid,)
            ).fetchone()
        if row is None or row["private_blob"] is None:
            raise TrustError("trust root has no private half; the vault cannot issue keys")
        raw = base64.urlsafe_b64decode(self._seal.decrypt(row["private_blob"]) + "==")
        return Ed25519PrivateKey.from_private_bytes(raw)

    # -- service identity ----------------------------------------------------

    def register_service(self, service_id: str) -> str:
        """Register a service and return its bearer token, once.

        Only the SHA-256 of the token is stored. There is nothing here to
        decrypt and nothing to hand back later, so a copy of ``keys.db`` does
        not yield a working credential — unlike sealing it, which would.
        """
        if not service_id.strip():
            raise TrustError("service_id is required")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO signing_services (service_id, token_sha256, created_at, lifecycle) "
                "VALUES (?,?,?,'active') "
                "ON CONFLICT(service_id) DO UPDATE SET token_sha256=excluded.token_sha256, "
                "created_at=excluded.created_at, lifecycle='active'",
                (service_id.strip(), digest, time.time()),
            )
            conn.commit()
        return token

    def verify_service(self, service_id: str, token: str) -> bool:
        """Constant-time check of a service bearer token."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT token_sha256, lifecycle FROM signing_services WHERE service_id=?",
                (service_id.strip(),),
            ).fetchone()
        if row is None or row["lifecycle"] != "active":
            # Still hash, so a missing service and a wrong token cost the same.
            hashlib.sha256(token.encode("utf-8")).hexdigest()
            return False
        offered = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(offered, row["token_sha256"])

    def revoke_service(self, service_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE signing_services SET lifecycle='revoked' WHERE service_id=?",
                (service_id.strip(),),
            )
            conn.commit()
            return cur.rowcount > 0

    # -- intermediates -------------------------------------------------------

    def issue_intermediate(
        self, subject: str, *, ttl_s: int = DEFAULT_INTERMEDIATE_TTL_S
    ) -> IssuedIntermediate:
        """Mint a short-lived signing key chained to the root.

        The private half is returned and then dropped: the row keeps only the
        public key, so nothing later can serve it again.
        """
        if not subject.strip():
            raise TrustError("subject is required")
        if ttl_s <= 0 or ttl_s > MAX_INTERMEDIATE_TTL_S:
            raise TrustError(f"ttl_s must be between 1 and {MAX_INTERMEDIATE_TTL_S}")

        root = self.ensure_root()
        root_private = self._root_private()

        private = Ed25519PrivateKey.generate()
        public_b64 = b64u(_raw_public(private.public_key()))
        now = int(time.time())
        expires = now + int(ttl_s)
        kid = f"int-{uuid.uuid4().hex[:16]}"
        chain = b64u(
            root_private.sign(
                binding_bytes(
                    kid=kid, subject=subject.strip(), public_key_b64=public_b64,
                    nbf=now, exp=expires,
                )
            )
        )

        record = SigningKeyRecord(
            kid=kid,
            kind="intermediate",
            subject=subject.strip(),
            public_key=public_b64,
            parent_kid=root.kid,
            chain_signature=chain,
            not_before=float(now),
            not_after=float(expires),
            lifecycle="active",
            created_at=float(now),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO signing_keys (kid, kind, subject, public_key, private_blob, "
                "parent_kid, chain_signature, not_before, not_after, lifecycle, created_at) "
                "VALUES (?,?,?,?,NULL,?,?,?,?,?,?)",
                (
                    record.kid,
                    record.kind,
                    record.subject,
                    record.public_key,
                    record.parent_kid,
                    record.chain_signature,
                    record.not_before,
                    record.not_after,
                    record.lifecycle,
                    record.created_at,
                ),
            )
            conn.commit()
        return IssuedIntermediate(record=record, private_key=b64u(_raw_private(private)))

    def revoke_key(self, kid: str, *, reason: str = "operator_revoke") -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE signing_keys SET lifecycle='revoked', revoked_reason=? "
                "WHERE kid=? AND kind='intermediate'",
                (reason, kid),
            )
            conn.commit()
            return cur.rowcount > 0

    def active_intermediates(self, *, now: float | None = None) -> list[SigningKeyRecord]:
        moment = time.time() if now is None else now
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signing_keys WHERE kind='intermediate' AND lifecycle='active' "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [r for r in (self._row(row) for row in rows) if r.is_usable(now=moment)]

    # -- publication ---------------------------------------------------------

    def jwks(self, *, now: float | None = None) -> dict[str, Any]:
        """The public document consumers verify against.

        Only intermediates appear. The root is published separately, at
        ``/keys/root``, so pinning it stays a deliberate act and a manifest
        signed by the root itself is accepted by nobody.
        """
        keys = []
        for record in self.active_intermediates(now=now):
            keys.append(
                {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "alg": "EdDSA",
                    "use": "sig",
                    "kid": record.kid,
                    "x": record.public_key,
                    "nbf": int(record.not_before),
                    "exp": int(record.not_after or 0),
                    # Chain material, so a consumer that pinned the root can
                    # verify this key offline instead of trusting the transport.
                    "netie_subject": record.subject,
                    "netie_issuer_kid": record.parent_kid,
                    "netie_chain_signature": record.chain_signature,
                }
            )
        return {"keys": keys}

    def root_document(self) -> dict[str, Any]:
        root = self.ensure_root()
        return {
            "kty": "OKP",
            "crv": "Ed25519",
            "alg": "EdDSA",
            "use": "sig",
            "kid": root.kid,
            "x": root.public_key,
            "netie_role": "trust-root",
            "created_at": int(root.created_at),
        }


def verify_chain(root_public_b64: str, jwk: dict[str, Any]) -> bool:
    """Check that a published intermediate really was vouched for by this root.

    Consumers do not have to call this — the JWKS is served over loopback — but
    an air-gapped or relayed consumer that pinned the root can, and the whole
    point of a trust root is that trusting the transport is optional.
    """
    try:
        root = Ed25519PublicKey.from_public_bytes(
            base64.urlsafe_b64decode(root_public_b64 + "==")
        )
        signature = base64.urlsafe_b64decode(str(jwk["netie_chain_signature"]) + "==")
        root.verify(
            signature,
            binding_bytes(
                kid=str(jwk["kid"]),
                subject=str(jwk.get("netie_subject", "")),
                public_key_b64=str(jwk["x"]),
                nbf=int(jwk["nbf"]),
                exp=int(jwk["exp"]),
            ),
        )
    except Exception:
        return False
    return True
