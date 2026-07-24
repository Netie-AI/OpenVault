"""Encrypted SQLite vault for provider API keys."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from openmw.openvault.vault.crypto import Seal, mask_secret
from openmw.openvault.paths import keys_db_path

ProviderKind = Literal[
    "openai",
    "anthropic",
    "openrouter",
    "groq",
    "google",
    "mistral",
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
    "custom",
]
KeyRole = Literal["primary", "backup", "cheap", "free"]
PrecheckStatus = Literal["unknown", "ok", "auth_fail", "rate_limit", "timeout", "error"]
KeyLifecycle = Literal["active", "revoked", "rotated", "compromised"]


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


class KeyVault:
    """CRUD + decrypt access for OpenVault keys."""

    def __init__(self, db_path: Path | None = None, seal: Seal | None = None) -> None:
        self._db_path = db_path if db_path is not None else keys_db_path()
        self._seal = seal if seal is not None else Seal()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS keys (
                  id TEXT PRIMARY KEY,
                  label TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  role TEXT NOT NULL,
                  base_url TEXT NOT NULL DEFAULT '',
                  secret_blob BLOB NOT NULL,
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
                  replaced_by TEXT
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
            conn.commit()

    def _row_to_record(self, row: sqlite3.Row, *, include_secret: bool = False) -> KeyRecord:
        secret = self._seal.decrypt(row["secret_blob"])
        # Columns guaranteed by _init_schema migration.
        return KeyRecord(
            id=row["id"],
            label=row["label"],
            provider=row["provider"],
            role=row["role"],
            base_url=row["base_url"],
            masked_secret=secret if include_secret else mask_secret(secret),
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
        )

    def list_keys(self, *, account_id: str | None = None) -> list[KeyRecord]:
        with self._connect() as conn:
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
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM keys WHERE id = ?", (key_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_secret(self, key_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT secret_blob FROM keys WHERE id = ?", (key_id,)).fetchone()
        if row is None:
            return None
        return self._seal.decrypt(row["secret_blob"])

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
    ) -> KeyRecord:
        key_id = uuid.uuid4().hex
        now = time.time()
        blob = self._seal.encrypt(secret)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO keys (
                  id, label, provider, role, base_url, secret_blob, enabled, priority,
                  precheck_status, created_at, updated_at, account_id, lifecycle
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?, ?, 'active')
                """,
                (
                    key_id,
                    label,
                    provider,
                    role,
                    base_url,
                    blob,
                    1 if enabled else 0,
                    priority,
                    now,
                    now,
                    account_id,
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
            fields.append("precheck_status = ?")
            values.append("unknown")
        fields.append("updated_at = ?")
        values.append(time.time())
        values.append(key_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE keys SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        return self.get(key_id)

    def delete(self, key_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM keys WHERE id = ?", (key_id,))
            conn.commit()
            return cur.rowcount > 0

    def revoke(self, key_id: str, *, reason: str = "operator_revoke") -> KeyRecord | None:
        """Disable and mark revoked — secret retained encrypted for audit until delete."""
        current = self.get(key_id)
        if current is None:
            return None
        with self._connect() as conn:
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
        replacement = self.create(
            label=f"{current.label} ({label_suffix})",
            provider=current.provider,
            secret=new_secret,
            role=current.role,
            base_url=current.base_url,
            priority=current.priority,
            enabled=True,
            account_id=current.account_id,
        )
        with self._connect() as conn:
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
            with self._connect() as conn:
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
        with self._connect() as conn:
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
        return [k for k in self.list_keys() if k.enabled and k.lifecycle == "active"]
