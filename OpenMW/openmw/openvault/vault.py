"""Encrypted SQLite vault for provider API keys."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openmw.openvault.crypto import Seal, mask_secret
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
                  updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def _row_to_record(self, row: sqlite3.Row, *, include_secret: bool = False) -> KeyRecord:
        secret = self._seal.decrypt(row["secret_blob"])
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
        )

    def list_keys(self) -> list[KeyRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM keys ORDER BY priority ASC, created_at ASC"
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
    ) -> KeyRecord:
        key_id = uuid.uuid4().hex
        now = time.time()
        blob = self._seal.encrypt(secret)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO keys (
                  id, label, provider, role, base_url, secret_blob, enabled, priority,
                  precheck_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?)
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
        return [k for k in self.list_keys() if k.enabled]
