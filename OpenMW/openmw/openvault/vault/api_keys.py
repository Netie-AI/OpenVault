"""Issued API keys — the credential someone else's app presents to FreeRoute.

Until now the gateway believed the caller about who they were: ``v1_chat`` read
``x-openfree-identity`` and ``x-openfree-tier`` straight off the request, and the
default tier is ``local`` at 6000 rpm / 6,000,000 tpm. So any caller could send
one header and buy themselves an unmetered pool of *our* vaulted provider keys,
or rotate the identity string for a fresh bucket every request. That is a
denial-of-wallet hole, and it is also why no usage number could ever be trusted:
there was no stable subject to attribute spend to.

An issued key fixes both. It is minted once, shown once, and stored only as a
SHA-256 digest — a stolen copy of ``keys.db`` yields no working credential. The
shape is deliberately the one already proven in ``vault/trust.py``
(``register_service`` / ``verify_service``); this is a second table in the same
database, not a second scheme and not a second vault (PRODUCT_ROLES lock 1).

What this module does NOT do: decide prices. It records a tier, and the tier
maps to limits in ``vault/ratelimit.py``. Billing needs the usage ledger and a
founder decision, in that order.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openmw.openvault.paths import keys_db_path

#: Every issued token starts with this, so a leaked one is greppable in logs and
#: a support ticket can say "that is an OpenVault key" without seeing the value.
TOKEN_PREFIX = "ov_"
#: Shown in listings so a human can tell two keys apart. Never enough to use.
_DISPLAY_CHARS = 6
_LABEL_RE = re.compile(r"^[\w .:@/-]{1,80}$")

#: ``local`` is the loopback developer tier — effectively unmetered. Issuing it
#: to a third party would hand out the exact bypass this module exists to close.
NON_ISSUABLE_TIERS = frozenset({"local"})


def issuable_tiers() -> frozenset[str]:
    """Tiers a key may be minted at: configured, minus the unmetered dev tier.

    An **allowlist**, not a denylist. Refusing only ``local`` looked sufficient
    and was not: an unknown tier name used to resolve to the default tier, which
    *is* ``local``, so ``tier="unlimited"`` bought exactly what refusing
    ``"local"`` was meant to prevent. The limiter now fails closed as well —
    two independent controls, because this one is worth getting wrong twice.
    """
    from openmw.openvault.vault.ratelimit import DEFAULT_TIERS

    return frozenset(DEFAULT_TIERS) - NON_ISSUABLE_TIERS


class ApiKeyError(ValueError):
    """Bad issue/revoke request — never a leak of whether a token exists."""


@dataclass(frozen=True)
class ApiKeyRecord:
    key_id: str
    label: str
    tier: str
    #: First few characters of the token, for display only.
    display: str
    created_at: float
    lifecycle: str = "active"
    last_used_at: float | None = None
    revoked_reason: str = ""

    @property
    def active(self) -> bool:
        return self.lifecycle == "active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_issued_token(value: str) -> bool:
    return value.startswith(TOKEN_PREFIX) and len(value) > len(TOKEN_PREFIX) + 8


class ApiKeyStore:
    """Mint, verify and revoke the keys third-party callers present."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path if db_path is not None else keys_db_path()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                  key_id TEXT PRIMARY KEY,
                  token_sha256 TEXT NOT NULL UNIQUE,
                  label TEXT NOT NULL,
                  tier TEXT NOT NULL,
                  display TEXT NOT NULL,
                  created_at REAL NOT NULL,
                  last_used_at REAL,
                  lifecycle TEXT NOT NULL DEFAULT 'active',
                  revoked_reason TEXT NOT NULL DEFAULT ''
                )
                """
            )
            # Verification looks up by digest on every single gateway request.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_digest ON api_keys(token_sha256)")
            conn.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> ApiKeyRecord:
        return ApiKeyRecord(
            key_id=row["key_id"],
            label=row["label"],
            tier=row["tier"],
            display=row["display"],
            created_at=float(row["created_at"]),
            lifecycle=row["lifecycle"],
            last_used_at=float(row["last_used_at"]) if row["last_used_at"] is not None else None,
            revoked_reason=row["revoked_reason"] or "",
        )

    # -- mint --------------------------------------------------------------

    def issue(self, *, label: str, tier: str = "free") -> tuple[ApiKeyRecord, str]:
        """Mint a key. Returns (record, token) — the token is never retrievable again."""
        clean_label = (label or "").strip()
        if not clean_label:
            raise ApiKeyError("label is required — an unnamed key cannot be revoked confidently")
        if not _LABEL_RE.match(clean_label):
            raise ApiKeyError("label may only contain letters, digits and . : @ / - _ space")
        clean_tier = (tier or "").strip().lower()
        if not clean_tier:
            raise ApiKeyError("tier is required")
        if clean_tier in NON_ISSUABLE_TIERS:
            raise ApiKeyError(
                f"{clean_tier!r} is the loopback developer tier and is effectively unmetered; "
                "issue a metered tier instead"
            )
        allowed = issuable_tiers()
        if clean_tier not in allowed:
            raise ApiKeyError(
                f"unknown tier {clean_tier!r}; issuable tiers are {', '.join(sorted(allowed))}"
            )

        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        record = ApiKeyRecord(
            key_id=uuid.uuid4().hex[:12],
            label=clean_label,
            tier=clean_tier,
            display=token[: len(TOKEN_PREFIX) + _DISPLAY_CHARS],
            created_at=time.time(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO api_keys (key_id, token_sha256, label, tier, display, created_at, "
                "last_used_at, lifecycle, revoked_reason) VALUES (?,?,?,?,?,?,NULL,'active','')",
                (
                    record.key_id,
                    token_digest(token),
                    record.label,
                    record.tier,
                    record.display,
                    record.created_at,
                ),
            )
            conn.commit()
        return record, token

    # -- verify ------------------------------------------------------------

    def verify(self, token: str) -> ApiKeyRecord | None:
        """Return the active record for this token, or None.

        Revoked and unknown are both None on purpose: a caller learns only that
        the credential does not work, never that it once did.
        """
        if not token:
            return None
        offered = token_digest(token)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE token_sha256=?", (offered,)).fetchone()
        if row is None:
            return None
        if not hmac.compare_digest(offered, row["token_sha256"]):
            return None
        record = self._row(row)
        return record if record.active else None

    def touch(self, key_id: str, *, when: float | None = None) -> None:
        """Record last use. Best-effort — never fails a request that worked."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE api_keys SET last_used_at=? WHERE key_id=?",
                (when if when is not None else time.time(), key_id),
            )
            conn.commit()

    # -- manage ------------------------------------------------------------

    def get(self, key_id: str) -> ApiKeyRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
        return self._row(row) if row is not None else None

    def list_keys(self, *, include_revoked: bool = True) -> list[ApiKeyRecord]:
        sql = "SELECT * FROM api_keys"
        if not include_revoked:
            sql += " WHERE lifecycle='active'"
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            return [self._row(r) for r in conn.execute(sql).fetchall()]

    def revoke(self, key_id: str, *, reason: str = "") -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE api_keys SET lifecycle='revoked', revoked_reason=? "
                "WHERE key_id=? AND lifecycle='active'",
                (reason.strip()[:200], key_id),
            )
            conn.commit()
            return cur.rowcount > 0


__all__ = [
    "NON_ISSUABLE_TIERS",
    "TOKEN_PREFIX",
    "ApiKeyError",
    "ApiKeyRecord",
    "ApiKeyStore",
    "issuable_tiers",
    "looks_like_issued_token",
    "token_digest",
]
