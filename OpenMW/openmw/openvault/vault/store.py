"""Encrypted SQLite vault for provider API keys."""

from __future__ import annotations

import base64
import contextlib
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from openmw.openvault.paths import keys_db_path
from openmw.openvault.vault.crypto import Seal, mask_secret

ProviderKind = Literal[
    "openai",
    "anthropic",
    "openrouter",
    "groq",
    "google",
    "mistral",
    "nvidia",
    "deepseek",
    "together",
    "fireworks",
    "cerebras",
    "huggingface",
    "ollama",
    "cortex",
    "litellm",
    "github_models",
    "siliconflow",
    "deepgram",
    "sea_lion",
    "custom",
]
KeyRole = Literal["primary", "backup", "cheap", "free"]
PrecheckStatus = Literal["unknown", "ok", "auth_fail", "rate_limit", "timeout", "error"]
KeyLifecycle = Literal["active", "revoked", "rotated", "compromised"]

#: Who owns the provider account behind this key, and therefore who pays.
#:
#: ``pooled`` is OpenVault's own key: the metered gateway may spend it, and we
#: carry the provider cost and the provider ToS exposure (DR-0009 option (a)).
#: ``tenant`` is a key somebody else uploaded. It is stored and it is usable by
#: its owner's own explicit operations, but it never enters the fallback pool,
#: so no metered caller can ever spend it.
#:
#: Existing rows backfill to ``pooled`` because before this column every key in
#: the vault was the operator's own.
KeyCustody = Literal["pooled", "tenant"]


@dataclass(frozen=True)
class KeyRecord:
    """Public view of a stored key (secret never included unless requested)."""

    id: str
    label: str
    provider: ProviderKind
    role: KeyRole
    base_url: str
    masked_secret: str
    enabled: bool
    priority: int
    precheck_status: PrecheckStatus
    last_latency_ms: float | None
    last_error: str | None
    last_precheck_at: float | None
    created_at: float
    updated_at: float
    account_id: str | None = None
    lifecycle: KeyLifecycle = "active"
    replaced_by: str | None = None
    custody: KeyCustody = "pooled"
    #: Service allowed to mint a lease for this kid. NULL until an admin assigns
    #: it. Not backfilled: assigning every existing kid would hand them out.
    owner_service_id: str | None = None
    #: Space this kid may be leased into. NULL until the same admin assign.
    owner_space: str | None = None
    #: Tenant (account id) this kid may be leased for. NULL until that assign.
    owner_tenant: str | None = None


class KeyVault:
    """CRUD + decrypt access for OpenVault keys."""

    def __init__(self, db_path: Path | None = None, seal: Seal | None = None) -> None:
        self._db_path = db_path if db_path is not None else keys_db_path()
        self._seal = seal if seal is not None else Seal()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @property
    def seal(self) -> Seal:
        """Shared crypto seal (lock/unseal state lives here)."""
        return self._seal

    @property
    def db_path(self) -> Path:
        """Filesystem path of the SQLite vault (shared with precheck_history)."""
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS keys (
                  id TEXT PRIMARY KEY,
                  label TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  role TEXT NOT NULL,
                  base_url TEXT NOT NULL DEFAULT '',
                  secret_blob BLOB NOT NULL,
                  masked TEXT NOT NULL DEFAULT '',
                  enabled INTEGER NOT NULL DEFAULT 1,
                  priority INTEGER NOT NULL DEFAULT 100,
                  precheck_status TEXT NOT NULL DEFAULT 'unknown',
                  last_latency_ms REAL,
                  last_error TEXT,
                  last_precheck_at REAL,
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL,
                  account_id TEXT,
                  lifecycle TEXT NOT NULL DEFAULT 'active',
                  replaced_by TEXT,
                  custody TEXT NOT NULL DEFAULT 'pooled',
                  key_fp TEXT,
                  owner_service_id TEXT,
                  owner_space TEXT,
                  owner_tenant TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vault_meta (
                  name TEXT PRIMARY KEY,
                  secret_blob BLOB NOT NULL
                )
                """
            )
            cols = {row[1] for row in conn.execute("PRAGMA table_info(keys)").fetchall()}
            if "account_id" not in cols:
                conn.execute("ALTER TABLE keys ADD COLUMN account_id TEXT")
            if "lifecycle" not in cols:
                conn.execute("ALTER TABLE keys ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'active'")
            if "replaced_by" not in cols:
                conn.execute("ALTER TABLE keys ADD COLUMN replaced_by TEXT")
            if "custody" not in cols:
                # Backfill 'pooled': every key that predates this column was the
                # operator's own. Defaulting the other way would silently empty
                # the fallback pool on upgrade and 503 every route.
                conn.execute("ALTER TABLE keys ADD COLUMN custody TEXT NOT NULL DEFAULT 'pooled'")
            if "masked" not in cols:
                conn.execute("ALTER TABLE keys ADD COLUMN masked TEXT NOT NULL DEFAULT ''")
            if "key_fp" not in cols:
                # HMAC of the provider key under a vault-held secret. Not a bare sha256.
                conn.execute("ALTER TABLE keys ADD COLUMN key_fp TEXT")
            if "owner_service_id" not in cols:
                # NULL, not a service id. Pre-lease kids stay unowned until
                # POST /api/keys/owner. A default owner would lease every kid.
                conn.execute("ALTER TABLE keys ADD COLUMN owner_service_id TEXT")
            if "owner_space" not in cols:
                # Additive. NULL means no Space is bound. Mint refuses that.
                conn.execute("ALTER TABLE keys ADD COLUMN owner_space TEXT")
            if "owner_tenant" not in cols:
                # Additive. The account id this kid may be leased for.
                conn.execute("ALTER TABLE keys ADD COLUMN owner_tenant TEXT")
            # One-time backfill: persist masks so list_keys never decrypts plaintext.
            # Skip while sealed — decrypt would fail closed, and masks stay empty
            # until an unseal + later write/backfill.
            if not self._seal.is_sealed:
                for row in conn.execute(
                    "SELECT id, secret_blob, masked FROM keys WHERE masked IS NULL OR masked = ''"
                ).fetchall():
                    secret = self._seal.decrypt(row["secret_blob"])
                    conn.execute(
                        "UPDATE keys SET masked = ? WHERE id = ?",
                        (mask_secret(secret), row["id"]),
                    )
            conn.commit()

    def _row_to_record(self, row: sqlite3.Row, *, include_secret: bool = False) -> KeyRecord:
        keys = row.keys()
        if include_secret:
            secret = self._seal.decrypt(row["secret_blob"])
            masked = secret
        else:
            stored = str(row["masked"]) if "masked" in keys and row["masked"] else ""
            if stored:
                masked = stored
            elif self._seal.is_sealed:
                masked = ""
            else:
                # Legacy row without mask — decrypt once; prefer schema backfill.
                secret = self._seal.decrypt(row["secret_blob"])
                masked = mask_secret(secret)
        return KeyRecord(
            id=row["id"],
            label=row["label"],
            provider=row["provider"],
            role=row["role"],
            base_url=row["base_url"],
            masked_secret=masked,
            enabled=bool(row["enabled"]),
            priority=int(row["priority"]),
            precheck_status=row["precheck_status"],
            last_latency_ms=row["last_latency_ms"],
            last_error=row["last_error"],
            last_precheck_at=row["last_precheck_at"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            account_id=row["account_id"],
            lifecycle=cast(KeyLifecycle, row["lifecycle"]),
            replaced_by=row["replaced_by"],
            custody=cast(
                KeyCustody,
                (str(row["custody"]) if "custody" in keys and row["custody"] else "pooled"),
            ),
            owner_service_id=(
                str(row["owner_service_id"])
                if "owner_service_id" in keys and row["owner_service_id"]
                else None
            ),
            owner_space=(
                str(row["owner_space"]) if "owner_space" in keys and row["owner_space"] else None
            ),
            owner_tenant=(
                str(row["owner_tenant"]) if "owner_tenant" in keys and row["owner_tenant"] else None
            ),
        )

    def list_keys(self, *, account_id: str | None = None) -> list[KeyRecord]:
        with contextlib.closing(self._connect()) as conn, conn:
            if account_id is None:
                rows = conn.execute(
                    "SELECT * FROM keys ORDER BY priority ASC, created_at ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM keys
                    WHERE account_id = ?
                    ORDER BY priority ASC, created_at ASC
                    """,
                    (account_id,),
                ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get(self, key_id: str) -> KeyRecord | None:
        with contextlib.closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT * FROM keys WHERE id = ?", (key_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_secret(self, key_id: str) -> str | None:
        with contextlib.closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT secret_blob FROM keys WHERE id = ?", (key_id,)).fetchone()
        if row is None:
            return None
        return self._seal.decrypt(row["secret_blob"])

    def get_or_create_fingerprint_secret(self) -> bytes:
        """Random HMAC key for this vault, encrypted at rest.

        Held by the vault so a provider-key fingerprint is not a bare hash.
        """
        name = "key_fp_hmac"
        with contextlib.closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT secret_blob FROM vault_meta WHERE name = ?",
                (name,),
            ).fetchone()
            if row is not None:
                return base64.b64decode(self._seal.decrypt(row["secret_blob"]))
            raw = secrets.token_bytes(32)
            blob = self._seal.encrypt(base64.b64encode(raw).decode("ascii"))
            conn.execute(
                "INSERT INTO vault_meta (name, secret_blob) VALUES (?, ?)",
                (name, blob),
            )
            conn.commit()
            return raw

    def get_fingerprint(self, key_id: str) -> str | None:
        """HMAC fingerprint stored beside the key, or None when unset."""
        with contextlib.closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT key_fp FROM keys WHERE id = ?", (key_id,)).fetchone()
        if row is None:
            return None
        value = row["key_fp"]
        if value is None or value == "":
            return None
        return str(value)

    def find_by_fingerprint(self, key_fp: str) -> KeyRecord | None:
        """First key row with this fingerprint, oldest first."""
        if not key_fp:
            return None
        with contextlib.closing(self._connect()) as conn, conn:
            row = conn.execute(
                """
                SELECT * FROM keys
                WHERE key_fp = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (key_fp,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def create(
        self,
        *,
        label: str,
        provider: ProviderKind,
        secret: str,
        role: KeyRole = "backup",
        base_url: str = "",
        priority: int = 100,
        enabled: bool = True,
        account_id: str | None = None,
        key_id: str | None = None,
        custody: KeyCustody = "pooled",
        key_fp: str | None = None,
    ) -> KeyRecord:
        key_id = key_id or uuid.uuid4().hex
        now = time.time()
        blob = self._seal.encrypt(secret)
        masked = mask_secret(secret)
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO keys (
                  id, label, provider, role, base_url, secret_blob, masked, enabled, priority,
                  precheck_status, created_at, updated_at, account_id, lifecycle, custody,
                  key_fp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?, ?, 'active', ?, ?)
                """,
                (
                    key_id,
                    label,
                    provider,
                    role,
                    base_url,
                    blob,
                    masked,
                    1 if enabled else 0,
                    priority,
                    now,
                    now,
                    account_id,
                    custody,
                    key_fp or None,
                ),
            )
            conn.commit()
        record = self.get(key_id)
        assert record is not None
        return record

    def update(
        self,
        key_id: str,
        *,
        label: str | None = None,
        secret: str | None = None,
        role: KeyRole | None = None,
        base_url: str | None = None,
        priority: int | None = None,
        enabled: bool | None = None,
        provider: ProviderKind | None = None,
        account_id: str | None = None,
    ) -> KeyRecord | None:
        current = self.get(key_id)
        if current is None:
            return None
        fields: list[str] = []
        values: list[object] = []
        if label is not None:
            fields.append("label = ?")
            values.append(label)
        if provider is not None:
            fields.append("provider = ?")
            values.append(provider)
        if role is not None:
            fields.append("role = ?")
            values.append(role)
        if base_url is not None:
            fields.append("base_url = ?")
            values.append(base_url)
        if priority is not None:
            fields.append("priority = ?")
            values.append(priority)
        if enabled is not None:
            fields.append("enabled = ?")
            values.append(1 if enabled else 0)
        if account_id is not None:
            fields.append("account_id = ?")
            values.append(account_id)
        if secret is not None:
            fields.append("secret_blob = ?")
            values.append(self._seal.encrypt(secret))
            fields.append("masked = ?")
            values.append(mask_secret(secret))
            fields.append("precheck_status = ?")
            values.append("unknown")
        fields.append("updated_at = ?")
        values.append(time.time())
        values.append(key_id)
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(f"UPDATE keys SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        return self.get(key_id)

    def set_owner_service(
        self,
        key_id: str,
        service_id: str | None,
        *,
        owner_space: str | None = None,
        owner_tenant: str | None = None,
    ) -> KeyRecord | None:
        """Assign or clear who may lease this kid.

        Empty ``service_id`` clears the service, the Space, and the tenant.
        A set service stores ``owner_space`` and ``owner_tenant`` beside it.
        Blank space or tenant is stored as NULL. The row is unchanged when
        the kid does not exist. Rotation does not call this.
        """
        if self.get(key_id) is None:
            return None
        owner = (service_id or "").strip() or None
        if owner is None:
            space = None
            tenant = None
        else:
            space = (owner_space or "").strip() or None
            tenant = (owner_tenant or "").strip() or None
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                """
                UPDATE keys
                SET owner_service_id = ?, owner_space = ?, owner_tenant = ?, updated_at = ?
                WHERE id = ?
                """,
                (owner, space, tenant, time.time(), key_id),
            )
            conn.commit()
        return self.get(key_id)

    def delete(self, key_id: str) -> bool:
        with contextlib.closing(self._connect()) as conn, conn:
            cur = conn.execute("DELETE FROM keys WHERE id = ?", (key_id,))
            conn.commit()
            return cur.rowcount > 0

    def revoke(self, key_id: str, *, reason: str = "operator_revoke") -> KeyRecord | None:
        """Disable and mark revoked — secret retained encrypted for audit until delete."""
        current = self.get(key_id)
        if current is None:
            return None
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                """
                UPDATE keys
                SET enabled = 0, lifecycle = 'revoked', last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (f"revoked:{reason}", time.time(), key_id),
            )
            conn.commit()
        return self.get(key_id)

    def rotate(
        self,
        key_id: str,
        *,
        new_secret: str,
        label_suffix: str = "rotated",
    ) -> KeyRecord | None:
        """Kill old key (rotated) and create a replacement under the same account."""
        current = self.get(key_id)
        if current is None:
            return None
        # The replacement kid is unowned. A lease is for one kid, not the label.
        replacement = self.create(
            label=f"{current.label} ({label_suffix})",
            provider=current.provider,
            secret=new_secret,
            role=current.role,
            base_url=current.base_url,
            priority=current.priority,
            enabled=True,
            account_id=current.account_id,
            custody=current.custody,
        )
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                """
                UPDATE keys
                SET enabled = 0, lifecycle = 'rotated', replaced_by = ?,
                    last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (replacement.id, "rotated:replaced", time.time(), key_id),
            )
            conn.commit()
        return replacement

    def incident_kill(
        self,
        account_id: str,
        *,
        reason: str = "compromised_or_manipulated",
        replacement_secrets: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Spin off: kill all account keys; optionally mint replacements for cloud providers.

        ``replacement_secrets`` maps provider id → new secret. Missing providers are
        listed as ``needs_register`` so the operator can paste fresh cloud keys.
        """
        killed: list[str] = []
        replacements: list[KeyRecord] = []
        needs_register: list[str] = []
        keys = self.list_keys(account_id=account_id)
        seen_providers: set[str] = set()
        for key in keys:
            if key.lifecycle in ("revoked", "compromised"):
                continue
            with contextlib.closing(self._connect()) as conn, conn:
                conn.execute(
                    """
                    UPDATE keys
                    SET enabled = 0, lifecycle = 'compromised', last_error = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (f"incident:{reason}", time.time(), key.id),
                )
                conn.commit()
            killed.append(key.id)
            seen_providers.add(str(key.provider))

        secrets = replacement_secrets or {}
        for provider in sorted(seen_providers):
            if provider in secrets:
                replacements.append(
                    self.create(
                        label=f"{provider} (incident replacement)",
                        provider=provider,  # type: ignore[arg-type]
                        secret=secrets[provider],
                        role="primary",
                        priority=10,
                        account_id=account_id,
                        custody="tenant",
                    )
                )
            elif provider not in ("ollama", "cortex", "litellm"):
                needs_register.append(provider)

        return {
            "account_id": account_id,
            "killed": killed,
            "replacements": [r.id for r in replacements],
            "needs_register": needs_register,
            "reason": reason,
        }

    def set_precheck(
        self,
        key_id: str,
        *,
        status: PrecheckStatus,
        latency_ms: float | None,
        error: str | None,
    ) -> None:
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                """
                UPDATE keys
                SET precheck_status = ?, last_latency_ms = ?, last_error = ?,
                    last_precheck_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, latency_ms, error, time.time(), time.time(), key_id),
            )
            conn.commit()

    def enabled_ordered(self) -> list[KeyRecord]:
        """Every enabled, active key regardless of who owns it.

        Not the spend path. Anything that selects a key to *spend* wants
        :meth:`pooled_ordered` — see the note there.
        """
        return [k for k in self.list_keys() if k.enabled and k.lifecycle == "active"]

    def pooled_ordered(self) -> list[KeyRecord]:
        """Enabled, active keys OpenVault itself owns and may spend.

        The metered gateway authenticates third-party callers with issued
        ``ov_`` keys, and every one of them walks this same list. Before the
        custody tag existed there was no owner filter at all, so tenant A's
        request could select a key tenant B had uploaded — latent with one
        operator and one pool, real the moment a second tenant holds a key.

        Per DR-0009 the gateway spends OpenVault's own pooled keys and carries
        the provider cost, so a key marked ``tenant`` is excluded here and can
        never be reached by a metered request.
        """
        return [k for k in self.enabled_ordered() if k.custody == "pooled"]
