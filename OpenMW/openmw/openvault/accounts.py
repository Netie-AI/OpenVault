"""Tenant account custody — Netie email, Google, private relay under OpenVault."""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

from openmw.openvault.paths import ensure_home

AuthProvider = Literal["netie_email", "email", "google"]
AccountStatus = Literal["active", "suspended", "compromised"]

_NETIE_DOMAIN = "netie.ai"
_RELAY_DOMAIN = "relay.netie.ai"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class AccountRecord:
    id: str
    display_name: str
    email: str
    auth_provider: AuthProvider
    relay_address: str | None
    status: AccountStatus
    operator_notes: str
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def accounts_db_path() -> Path:
    return ensure_home() / "accounts.db"


def suggest_netie_email(local_part: str) -> str:
    """Prefer a native Netie mailbox so custody stays under the platform."""
    cleaned = re.sub(r"[^a-z0-9._+-]+", "", local_part.lower())
    if not cleaned:
        cleaned = f"user-{uuid.uuid4().hex[:8]}"
    return f"{cleaned}@{_NETIE_DOMAIN}"


def _validate_email(email: str) -> str:
    normalized = email.strip().lower()
    if not _EMAIL_RE.match(normalized):
        raise ValueError(f"invalid email: {email!r}")
    return normalized


class AccountStore:
    """SQLite store for customer accounts + private relay mapping."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path if db_path is not None else accounts_db_path()
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
                CREATE TABLE IF NOT EXISTS accounts (
                  id TEXT PRIMARY KEY,
                  display_name TEXT NOT NULL,
                  email TEXT NOT NULL UNIQUE,
                  auth_provider TEXT NOT NULL,
                  relay_address TEXT UNIQUE,
                  status TEXT NOT NULL DEFAULT 'active',
                  operator_notes TEXT NOT NULL DEFAULT '',
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def _row(self, row: sqlite3.Row) -> AccountRecord:
        return AccountRecord(
            id=row["id"],
            display_name=row["display_name"],
            email=row["email"],
            auth_provider=cast(AuthProvider, row["auth_provider"]),
            relay_address=row["relay_address"],
            status=cast(AccountStatus, row["status"]),
            operator_notes=row["operator_notes"] or "",
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def create(
        self,
        *,
        display_name: str,
        email: str | None = None,
        auth_provider: AuthProvider = "netie_email",
        local_part: str | None = None,
        operator_notes: str = "",
        allocate_relay: bool = True,
    ) -> AccountRecord:
        """Create an account. Prefer ``netie_email`` so keys/apps live under Netie custody."""
        if auth_provider == "netie_email":
            base = local_part or (email.split("@")[0] if email else display_name)
            final_email = suggest_netie_email(base)
        elif email:
            final_email = _validate_email(email)
        else:
            raise ValueError("email is required for email/google signup")

        account_id = uuid.uuid4().hex
        now = time.time()
        relay: str | None = None
        if allocate_relay:
            relay = f"{uuid.uuid4().hex[:12]}@{_RELAY_DOMAIN}"

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                  id, display_name, email, auth_provider, relay_address,
                  status, operator_notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    account_id,
                    display_name.strip() or final_email,
                    final_email,
                    auth_provider,
                    relay,
                    operator_notes,
                    now,
                    now,
                ),
            )
            conn.commit()
        record = self.get(account_id)
        assert record is not None
        return record

    def get(self, account_id: str) -> AccountRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            return None
        return self._row(row)

    def get_by_email(self, email: str) -> AccountRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE email = ?", (_validate_email(email),)
            ).fetchone()
        if row is None:
            return None
        return self._row(row)

    def list_accounts(self) -> list[AccountRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def allocate_relay(self, account_id: str) -> AccountRecord | None:
        current = self.get(account_id)
        if current is None:
            return None
        relay = f"{uuid.uuid4().hex[:12]}@{_RELAY_DOMAIN}"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE accounts
                SET relay_address = ?, updated_at = ?
                WHERE id = ?
                """,
                (relay, time.time(), account_id),
            )
            conn.commit()
        return self.get(account_id)

    def set_status(
        self,
        account_id: str,
        status: AccountStatus,
        *,
        operator_notes: str | None = None,
    ) -> AccountRecord | None:
        current = self.get(account_id)
        if current is None:
            return None
        notes = current.operator_notes if operator_notes is None else operator_notes
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE accounts
                SET status = ?, operator_notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, notes, time.time(), account_id),
            )
            conn.commit()
        return self.get(account_id)

    def operator_access_bundle(self, account_id: str) -> dict[str, Any] | None:
        """Everything an operator needs to help the tenant (keys filled by caller)."""
        account = self.get(account_id)
        if account is None:
            return None
        return {
            "account": account.to_dict(),
            "custody": {
                "preferred_signup": "netie_email",
                "private_relay": account.relay_address,
                "platform_owned": account.auth_provider == "netie_email"
                or (account.email.endswith(f"@{_NETIE_DOMAIN}")),
                "operator_can": [
                    "create_and_store_keys",
                    "run_precheck",
                    "rotate_or_kill_keys",
                    "replace_cloud_keys",
                    "run_openship_deploy",
                ],
            },
        }
