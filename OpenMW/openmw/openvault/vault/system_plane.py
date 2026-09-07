"""SYSTEM control plane: entitlements, routing, unlock, metering, seats.

OpenVault owns this policy surface. Marketing owns the public rate page on
netie.ai -- this module is not that page, and it does not bind :5000 publicly.

Locked display SKUs come from GitHub #48. Usage $/unit is intentionally unset
(USAGE_UNIT_USD is None / NEEDS-YOU). Do not invent a number to make a bill.
Ultra/Giga reuse the existing ``pro`` limiter tier because rpm/tpm for those
SKUs was not locked; the 20% usage-credit discount is the locked differentiator.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from openmw.openvault.vault.accounts import AccountStore, accounts_db_path
from openmw.openvault.vault.usage_store import (
    CURRENCY_CODE,
    CURRENCY_SIGN,
    USAGE_UNIT_PREFIX,
    USAGE_UNIT_SIGN,
    USAGE_UNIT_STATUS,
    USAGE_UNIT_USD,
    usd_prefix,
    usd_sign,
)

PlanFamily = Literal["individual", "team"]
LimiterTier = Literal["free", "pro"]

POLICY_VERSION = 2
#: Internal writers host -- not a public :5000 bind.
INTERNAL_WRITERS_URL = "http://35.253.229.206:8080"
DEFAULT_BIND_HOST = "127.0.0.1"
CUSTODY_PORT = 5000
PUBLIC_BIND_HOSTS = frozenset({"0.0.0.0", "::", "[::]"})
SEAT_USD = 30
USAGE_CREDIT_DISCOUNT_PCT = 20
LOCKED_PLAN = ""

_PUBLIC_BIND_ENV = "OPENVAULT_ALLOW_PUBLIC_BIND"


class SystemPlaneError(ValueError):
    """Bad unlock / seat request -- never a leak of whether an account exists."""


@dataclass(frozen=True)
class PlanSpec:
    """One locked display SKU. ``display_usd`` is the catalog number, not usage."""

    id: str
    display_name: str
    family: PlanFamily
    display_usd: int
    limiter_tier: LimiterTier
    usage_credit_discount: bool

    @property
    def usage_credit_factor(self) -> float:
        if self.usage_credit_discount:
            return (100 - USAGE_CREDIT_DISCOUNT_PCT) / 100.0
        return 1.0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["usage_credit_factor"] = self.usage_credit_factor
        payload["usage_credit_discount_pct"] = (
            USAGE_CREDIT_DISCOUNT_PCT if self.usage_credit_discount else 0
        )
        payload["currency"] = CURRENCY_CODE
        payload["currency_sign"] = CURRENCY_SIGN
        payload["display_usd_prefix"] = usd_prefix(self.display_usd)
        payload["display_usd_sign"] = usd_sign(self.display_usd)
        payload["seat_usd"] = SEAT_USD if self.family == "team" else 0
        payload["seat_usd_prefix"] = usd_prefix(SEAT_USD) if self.family == "team" else ""
        payload["seat_usd_sign"] = usd_sign(SEAT_USD) if self.family == "team" else ""
        return payload


# Individual: Basic USD10 / Pro USD100 / Ultra USD500
# Team display: USD10 / Team Ultra USD500 / Team Giga USD1000
PLANS: dict[str, PlanSpec] = {
    "basic": PlanSpec("basic", "Basic", "individual", 10, "free", False),
    "pro": PlanSpec("pro", "Pro", "individual", 100, "pro", False),
    "ultra": PlanSpec("ultra", "Ultra", "individual", 500, "pro", True),
    "team": PlanSpec("team", "Team", "team", 10, "free", False),
    "team_ultra": PlanSpec("team_ultra", "Team Ultra", "team", 500, "pro", True),
    "team_giga": PlanSpec("team_giga", "Team Giga", "team", 1000, "pro", True),
}

ULTRA_GIGA_PLAN_IDS = frozenset(p.id for p in PLANS.values() if p.usage_credit_discount)


@dataclass(frozen=True)
class Entitlement:
    account_id: str
    plan_id: str
    seats: int
    unlocked_at: float | None
    updated_at: float

    @property
    def unlocked(self) -> bool:
        return bool(self.plan_id) and self.plan_id in PLANS

    @property
    def plan(self) -> PlanSpec | None:
        return PLANS.get(self.plan_id)

    def to_dict(self) -> dict[str, Any]:
        plan = self.plan
        monthly = monthly_display_usd(self.plan_id, self.seats)
        seat_amount = SEAT_USD if plan is not None and plan.family == "team" else 0
        return {
            "account_id": self.account_id,
            "plan_id": self.plan_id or None,
            "unlocked": self.unlocked,
            "seats": self.seats,
            "unlocked_at": self.unlocked_at,
            "updated_at": self.updated_at,
            "family": plan.family if plan is not None else None,
            "currency": CURRENCY_CODE,
            "currency_sign": CURRENCY_SIGN,
            "display_usd": plan.display_usd if plan is not None else None,
            "display_usd_prefix": usd_prefix(plan.display_usd) if plan is not None else None,
            "display_usd_sign": usd_sign(plan.display_usd) if plan is not None else None,
            "monthly_display_usd": monthly,
            "monthly_display_usd_prefix": usd_prefix(monthly) if monthly is not None else None,
            "monthly_display_usd_sign": usd_sign(monthly) if monthly is not None else None,
            "seat_usd": seat_amount,
            "seat_usd_prefix": usd_prefix(seat_amount) if seat_amount else "",
            "seat_usd_sign": usd_sign(seat_amount) if seat_amount else "",
            "limiter_tier": plan.limiter_tier if plan is not None else None,
            "usage_credit_factor": usage_credit_factor(self.plan_id),
            "usage_credit_discount_pct": (
                USAGE_CREDIT_DISCOUNT_PCT if self.plan_id in ULTRA_GIGA_PLAN_IDS else 0
            ),
        }


def usage_credit_factor(plan_id: str) -> float:
    """1.0 on Pro and USD30/seat; 0.8 on Ultra/Giga. Never a dollar amount."""
    plan = PLANS.get(plan_id)
    if plan is None:
        return 1.0
    return plan.usage_credit_factor


def monthly_display_usd(plan_id: str, seats: int) -> int | None:
    """Locked display SKU + team seats. None when the account is locked."""
    plan = PLANS.get(plan_id)
    if plan is None:
        return None
    if plan.family == "individual":
        return plan.display_usd
    return plan.display_usd + max(0, int(seats)) * SEAT_USD


def bind_is_public(host: str) -> bool:
    return (host or "").strip().lower() in PUBLIC_BIND_HOSTS


def public_bind_allowed() -> bool:
    """Public :5000 stays off unless an operator sets the explicit escape hatch."""
    return os.environ.get(_PUBLIC_BIND_ENV, "").strip() == "1"


def require_private_bind(host: str) -> str:
    """Custody API bind. Default 127.0.0.1; 0.0.0.0 is a deliberate exception."""
    cleaned = (host or "").strip() or DEFAULT_BIND_HOST
    if bind_is_public(cleaned) and not public_bind_allowed():
        raise SystemPlaneError(
            "public :5000 bind is off; custody API stays on 127.0.0.1. "
            f"Internal writers use {INTERNAL_WRITERS_URL}. "
            f"Set {_PUBLIC_BIND_ENV}=1 only for a deliberate exception."
        )
    return cleaned


def bind_policy() -> dict[str, Any]:
    writers = INTERNAL_WRITERS_URL.rstrip("/")
    live_key_id = _safe_live_key_id()
    return {
        "public_bind_allowed": False,
        "default_host": DEFAULT_BIND_HOST,
        "custody_port": CUSTODY_PORT,
        "internal_writers_url": INTERNAL_WRITERS_URL,
        "internal_writers_only": True,
        "public_rate_page": False,
        "escape_hatch_env": _PUBLIC_BIND_ENV,
        # Cortex prove fetches JWKS here. Public pin kids. Intermediate mint
        # stays loopback; POST /keys/services also allows OPENVAULT_SERVICES_ALLOW.
        "jwks_uri": "/.well-known/jwks.json",
        "jwks_alt": "/keys/jwks",
        "root_uri": "/keys/root",
        "jwks_url": f"{writers}/.well-known/jwks.json",
        "mint_loopback_only": True,
        "services_allow_env": "OPENVAULT_SERVICES_ALLOW",
        "live_key_id_env": "LIVE_KEY_ID",
        "live_key_secret_manager": "openvault-dms-writer-token",
        "live_key_id": live_key_id,
    }


def _safe_live_key_id() -> str | None:
    """Echo the sealed caller id only. Never an ``ov_`` token."""
    raw = (os.environ.get("OPENVAULT_LIVE_KEY_ID") or os.environ.get("LIVE_KEY_ID") or "").strip()
    if not raw or raw.startswith("ov_") or len(raw) > 64:
        return None
    return raw


def catalog_payload() -> dict[str, Any]:
    """Internal writers catalog. Not a public marketing page."""
    return {
        "ok": True,
        "surface": "system_control_plane",
        "policy_version": POLICY_VERSION,
        "public_rate_page": False,
        "owner": "openvault",
        "marketing_owner": "netie.ai",
        "currency": CURRENCY_CODE,
        "currency_sign": CURRENCY_SIGN,
        "plans": {plan_id: spec.to_dict() for plan_id, spec in PLANS.items()},
        "seat_usd": SEAT_USD,
        "seat_usd_prefix": usd_prefix(SEAT_USD),
        "seat_usd_sign": usd_sign(SEAT_USD),
        "usage": {
            "currency": CURRENCY_CODE,
            "currency_sign": CURRENCY_SIGN,
            "unit_usd": USAGE_UNIT_USD,
            "unit_status": USAGE_UNIT_STATUS,
            "unit_prefix": USAGE_UNIT_PREFIX,
            "unit_sign": USAGE_UNIT_SIGN,
            "credit_discount_pct_ultra_giga": USAGE_CREDIT_DISCOUNT_PCT,
            "credit_normal_on": ["pro", "seat"],
            "priced": False,
        },
        "bind": bind_policy(),
    }


def route_decision(entitlement: Entitlement) -> dict[str, Any]:
    """Which existing gateway limiter this entitlement maps to.

    Does not walk the provider pool and does not invent rpm/tpm.
    """
    plan = entitlement.plan
    if plan is None or not entitlement.unlocked:
        return {
            "allowed": False,
            "account_id": entitlement.account_id,
            "plan_id": None,
            "limiter_tier": None,
            "usage_credit_factor": 1.0,
            "intents": [],
            "reason": "locked",
        }
    return {
        "allowed": True,
        "account_id": entitlement.account_id,
        "plan_id": plan.id,
        "limiter_tier": plan.limiter_tier,
        "usage_credit_factor": plan.usage_credit_factor,
        "intents": ["connect", "invoke"],
        "reason": "unlocked",
        "family": plan.family,
    }


def metering_overlay(entitlement: Entitlement, ledger: dict[str, Any]) -> dict[str, Any]:
    """Policy overlay on the usage ledger. Never multiplies tokens into dollars."""
    factor = usage_credit_factor(entitlement.plan_id)
    discount_pct = USAGE_CREDIT_DISCOUNT_PCT if factor < 1.0 else 0
    return {
        "ok": True,
        "account_id": entitlement.account_id,
        "plan_id": entitlement.plan_id or None,
        "unlocked": entitlement.unlocked,
        "priced": False,
        "currency": CURRENCY_CODE,
        "currency_sign": CURRENCY_SIGN,
        "usage_unit_usd": USAGE_UNIT_USD,
        "usage_unit_status": USAGE_UNIT_STATUS,
        "usage_unit_prefix": USAGE_UNIT_PREFIX,
        "usage_unit_sign": USAGE_UNIT_SIGN,
        "usage_credit_factor": factor,
        "usage_credit_discount_pct": discount_pct,
        "seat_usd": SEAT_USD,
        "seat_usd_prefix": usd_prefix(SEAT_USD),
        "seat_usd_sign": usd_sign(SEAT_USD),
        "seats": entitlement.seats,
        "ledger_scope": "process",
        "ledger": ledger,
        "note": (
            "issued keys are not bound to accounts; token rows stay on api_key_id. "
            "usage $/unit is USD NEEDS-YOU -- this overlay never invents a bill."
        ),
    }


class EntitlementStore:
    """Plan + seats for an account. Same accounts.db -- not a second vault."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path if db_path is not None else accounts_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entitlements (
                  account_id TEXT PRIMARY KEY,
                  plan_id TEXT NOT NULL DEFAULT '',
                  seats INTEGER NOT NULL DEFAULT 0,
                  unlocked_at REAL,
                  updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def _empty(self, account_id: str) -> Entitlement:
        return Entitlement(
            account_id=account_id,
            plan_id=LOCKED_PLAN,
            seats=0,
            unlocked_at=None,
            updated_at=0.0,
        )

    def _row(self, row: sqlite3.Row) -> Entitlement:
        unlocked_raw = row["unlocked_at"]
        return Entitlement(
            account_id=row["account_id"],
            plan_id=row["plan_id"] or LOCKED_PLAN,
            seats=int(row["seats"]),
            unlocked_at=float(unlocked_raw) if unlocked_raw is not None else None,
            updated_at=float(row["updated_at"]),
        )

    def get(self, account_id: str) -> Entitlement:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM entitlements WHERE account_id=?", (account_id,)
            ).fetchone()
        if row is None:
            return self._empty(account_id)
        return self._row(row)

    def unlock(
        self,
        account_id: str,
        plan_id: str,
        *,
        seats: int | None = None,
        accounts: AccountStore,
    ) -> Entitlement:
        if accounts.get(account_id) is None:
            raise SystemPlaneError("account not found")
        clean = (plan_id or "").strip().lower()
        plan = PLANS.get(clean)
        if plan is None:
            raise SystemPlaneError(
                f"unknown plan {plan_id!r}; known plans are {', '.join(sorted(PLANS))}"
            )
        current = self.get(account_id)
        if plan.family == "individual":
            if seats is not None:
                raise SystemPlaneError("individual plans do not take team seats")
            next_seats = 0
        elif seats is not None:
            next_seats = _require_team_seats(seats)
        elif current.seats >= 1:
            next_seats = current.seats
        else:
            next_seats = 1
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO entitlements (account_id, plan_id, seats, unlocked_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                  plan_id=excluded.plan_id,
                  seats=excluded.seats,
                  unlocked_at=excluded.unlocked_at,
                  updated_at=excluded.updated_at
                """,
                (account_id, plan.id, next_seats, now, now),
            )
            conn.commit()
        return self.get(account_id)

    def lock(self, account_id: str, *, accounts: AccountStore) -> Entitlement:
        if accounts.get(account_id) is None:
            raise SystemPlaneError("account not found")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO entitlements (account_id, plan_id, seats, unlocked_at, updated_at)
                VALUES (?, '', 0, NULL, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                  plan_id='',
                  seats=0,
                  unlocked_at=NULL,
                  updated_at=excluded.updated_at
                """,
                (account_id, now),
            )
            conn.commit()
        return self.get(account_id)

    def set_seats(self, account_id: str, seats: int, *, accounts: AccountStore) -> Entitlement:
        if accounts.get(account_id) is None:
            raise SystemPlaneError("account not found")
        current = self.get(account_id)
        plan = current.plan
        if plan is None or plan.family != "team":
            raise SystemPlaneError("seats are a team SKU; unlock a team plan first")
        next_seats = _require_team_seats(seats)
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE entitlements SET seats=?, updated_at=? WHERE account_id=?",
                (next_seats, now, account_id),
            )
            conn.commit()
        return self.get(account_id)


def _require_team_seats(seats: int) -> int:
    if int(seats) != seats:
        raise SystemPlaneError("seats must be a whole number")
    value = int(seats)
    if value < 1:
        raise SystemPlaneError("a team plan needs at least one seat")
    return value


__all__ = [
    "CUSTODY_PORT",
    "DEFAULT_BIND_HOST",
    "INTERNAL_WRITERS_URL",
    "PLANS",
    "POLICY_VERSION",
    "SEAT_USD",
    "ULTRA_GIGA_PLAN_IDS",
    "USAGE_CREDIT_DISCOUNT_PCT",
    "Entitlement",
    "EntitlementStore",
    "PlanSpec",
    "SystemPlaneError",
    "bind_is_public",
    "bind_policy",
    "catalog_payload",
    "metering_overlay",
    "monthly_display_usd",
    "public_bind_allowed",
    "require_private_bind",
    "route_decision",
    "usage_credit_factor",
]
