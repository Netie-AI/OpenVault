"""Tenant seats, manager scope, allowance, and incremental usage counters.

Off unless ``OV_SEAT_PLANE`` is the single digit ``1``. Anything else, including
``true`` and ``yes``, leaves the process ledger and the per-request
``summary()`` scan exactly as they are.

Keys stay in ``api_keys``. A seat binding is a separate row. A manager token
is ``ovm_`` and is stored only as SHA-256. It is not an issued ``ov_`` key.

``OV_SEAT_POLICY`` is an optional JSON file. A bad file does not fail the
gateway: the last good parse for that path stays in effect and the error is
reported on the policy route. Chat traffic is not turned into 503.
"""

from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import structlog

from openmw.openvault.paths import keys_db_path
from openmw.openvault.vault.accounts import AccountStore
from openmw.openvault.vault.route_policy import ROUTE_ROLES
from openmw.openvault.vault.system_plane import EntitlementStore, metering_overlay
from openmw.openvault.vault.usage_store import (
    CURRENCY_CODE,
    CURRENCY_SIGN,
    USAGE_UNIT_PREFIX,
    USAGE_UNIT_SIGN,
    USAGE_UNIT_STATUS,
    USAGE_UNIT_USD,
    UsageEvent,
)

log = structlog.get_logger()

FLAG_ENV = "OV_SEAT_PLANE"
POLICY_ENV = "OV_SEAT_POLICY"
MANAGER_HEADER = "X-OpenVault-Manager"
MANAGER_PREFIX = "ovm_"
DEFAULT_MARKUP = 1.2
DEFAULT_RETENTION_DAYS = 30
QUESTION_MAX_CHARS = 2000
_LABEL_RE = re.compile(r"^[\w .:@/-]{1,80}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_READY_BUCKET = "__ready__"
_ABSENT_DIGEST = "0" * 64
_LOCK = threading.Lock()
_LAST_GOOD: dict[str, dict[str, Any]] = {}

_MANAGER_EXACT = frozenset({"/api/system/metering", "/api/usage"})
_MANAGER_PREFIX = "/api/system/tenant/"


class _BillableStore(Protocol):
    def summary(
        self,
        *,
        api_key_id: str | None = None,
        identity: str | None = None,
        since: float | None = None,
    ) -> dict[str, Any]: ...

    def counter_billable(self, api_key_id: str) -> int: ...


class SeatError(ValueError):
    """Manager or operator request that must not become a process-wide 503."""

    def __init__(self, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


@dataclass(frozen=True)
class SeatBlock:
    """A seated request that must not go upstream."""

    status: int
    error_type: str
    message: str
    reason: str
    seat_id: str
    cap: str = ""


@dataclass(frozen=True)
class Actor:
    """Who is changing or reading a tenant. ``who`` is an id, never a token."""

    kind: str
    tenant_id: str | None
    who: str


@dataclass(frozen=True)
class Manager:
    manager_id: str
    tenant_id: str
    label: str


@dataclass(frozen=True)
class PolicyState:
    body: dict[str, Any]
    error: str | None
    using: str


@dataclass(frozen=True)
class Effective:
    allowance_usd: float
    ceiling_usd: float | None
    daily_usd: float | None
    monthly_usd: float | None
    models: list[str] | None
    roles: list[str] | None
    markup: float
    capture: bool
    retention_days: int


def seat_plane_enabled() -> bool:
    """True only when the flag is the digit 1. Default off."""
    return (os.environ.get(FLAG_ENV) or "").strip() == "1"


def overage_usd(metered: float, allowance: float, markup: float) -> float:
    """Billable overage. Included allowance is not marked up."""
    extra = metered - allowance
    if extra <= 0:
        return 0.0
    return round(extra * markup, 6)


def reset_seat_policy_cache() -> None:
    """Drop the in-process last-good file. Tests isolate the policy path."""
    with _LOCK:
        _LAST_GOOD.clear()


def manager_surface(path: str) -> bool:
    """Routes a tenant manager may call. Everything else stays on the old guard."""
    if path in _MANAGER_EXACT:
        return True
    return path.startswith(_MANAGER_PREFIX)


def _policy_name(path: Path) -> str:
    return path.name or "seat-policy.json"


def current_policy() -> PolicyState:
    """Last good file for this path. Never raises. Never a reason to 503."""
    raw = (os.environ.get(POLICY_ENV) or "").strip()
    if not raw:
        return PolicyState(body={}, error=None, using="none")
    path = Path(raw)
    name = _policy_name(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("seat_policy_rejected", path=str(path), error_type=type(exc).__name__)
        return _policy_fallback(path, f"cannot read seat policy {name}")
    try:
        parsed = _parse_policy(json.loads(text), name)
    except ValueError as exc:
        log.warning("seat_policy_rejected", path=str(path), error_type=type(exc).__name__)
        return _policy_fallback(path, f"seat policy {name} is not valid")
    with _LOCK:
        _LAST_GOOD[str(path)] = parsed
    return PolicyState(body=parsed, error=None, using="file")


def _policy_fallback(path: Path, error: str) -> PolicyState:
    with _LOCK:
        good = _LAST_GOOD.get(str(path))
    if good is not None:
        return PolicyState(body=good, error=error, using="last_good")
    return PolicyState(body={}, error=error, using="none")


def _parse_policy(data: object, name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"seat policy {name} is not valid")
    out: dict[str, Any] = {}
    if "markup" in data:
        out["markup"] = _require_markup(data["markup"], "markup")
    if "seats" in data:
        seats = data["seats"]
        if not isinstance(seats, dict):
            raise ValueError(f"seat policy {name} is not valid")
        parsed: dict[str, dict[str, Any]] = {}
        for seat_id, raw in seats.items():
            parsed[str(seat_id)] = _parse_seat_file(raw, name)
        out["seats"] = parsed
    if "tenants" in data:
        tenants = data["tenants"]
        if not isinstance(tenants, dict):
            raise ValueError(f"seat policy {name} is not valid")
        parsed_tenants: dict[str, dict[str, Any]] = {}
        for tenant_id, raw in tenants.items():
            parsed_tenants[str(tenant_id)] = _parse_tenant_file(raw, name)
        out["tenants"] = parsed_tenants
    return out


def _parse_seat_file(raw: object, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"seat policy {name} is not valid")
    out: dict[str, Any] = {}
    for key in ("allowance_usd", "ceiling_usd", "daily_usd", "monthly_usd"):
        if key in raw:
            out[key] = _require_usd(raw[key], key)
    if "models" in raw:
        out["models"] = _name_list(raw["models"], name)
    if "roles" in raw:
        out["roles"] = _role_list(raw["roles"], name)
    return out


def _parse_tenant_file(raw: object, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"seat policy {name} is not valid")
    out: dict[str, Any] = {}
    if "markup" in raw:
        out["markup"] = _require_markup(raw["markup"], "markup")
    if "question_capture" in raw:
        if not isinstance(raw["question_capture"], bool):
            raise ValueError(f"seat policy {name} is not valid")
        out["question_capture"] = raw["question_capture"]
    if "retention_days" in raw:
        out["retention_days"] = _require_days(raw["retention_days"], name)
    return out


def _require_markup(value: object, where: str) -> float:
    number = _finite(value, where)
    if number <= 0 or number > 100:
        raise ValueError(f"{where} must be a finite number above 0 and at most 100")
    return number


def _require_usd(value: object, where: str) -> float:
    number = _finite(value, where)
    if number < 0 or number > 1_000_000_000:
        raise ValueError(f"{where} must be a finite number from 0 to 1000000000")
    return number


def _finite(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{where} must be a finite number")
    return number


def _require_days(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"seat policy {name} is not valid")
    if value < 0 or value > 3650:
        raise ValueError(f"seat policy {name} is not valid")
    return value


def _name_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError(f"seat policy {name} is not valid")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"seat policy {name} is not valid")
        cleaned = item.strip()
        if not cleaned or len(cleaned) > 128 or cleaned in out:
            raise ValueError(f"seat policy {name} is not valid")
        out.append(cleaned)
    return out


def _role_list(value: object, name: str) -> list[str]:
    names = _name_list(value, name)
    allowed = set(ROUTE_ROLES)
    if any(role not in allowed for role in names):
        raise ValueError(f"seat policy {name} is not valid")
    return names


def _label(value: str, where: str) -> str:
    cleaned = (value or "").strip()
    if not _LABEL_RE.match(cleaned):
        raise SeatError(f"{where} may only contain letters, digits and . : @ / - _ space")
    return cleaned


def question_text(messages: object) -> str:
    """Last user turn, trimmed. Empty when there is nothing to store."""
    if not isinstance(messages, list):
        return ""
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            cleaned = content.strip()
            if cleaned:
                return cleaned[:QUESTION_MAX_CHARS]
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(str(part.get("text")))
            cleaned = " ".join(part.strip() for part in parts if part.strip())
            if cleaned:
                return cleaned[:QUESTION_MAX_CHARS]
    return ""


def buckets_for(created_at: float) -> tuple[str, str]:
    """UTC month and day buckets. Same strings SQLite ``unixepoch`` prints."""
    stamp = time.gmtime(created_at)
    return time.strftime("%Y-%m", stamp), time.strftime("%Y-%m-%d", stamp)


def month_window(month: str) -> tuple[float, float]:
    """Half-open UTC window for ``YYYY-MM``."""
    if not _MONTH_RE.match(month or ""):
        raise SeatError("month must be YYYY-MM")
    year = int(month[0:4])
    mon = int(month[5:7])
    if mon < 1 or mon > 12:
        raise SeatError("month must be YYYY-MM")
    start = float(calendar.timegm((year, mon, 1, 0, 0, 0)))
    if mon == 12:
        end = float(calendar.timegm((year + 1, 1, 1, 0, 0, 0)))
    else:
        end = float(calendar.timegm((year, mon + 1, 1, 0, 0, 0)))
    return start, end


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seat_tenants (
          tenant_id TEXT PRIMARY KEY,
          account_id TEXT NOT NULL UNIQUE,
          label TEXT NOT NULL,
          markup REAL NOT NULL DEFAULT 1.2,
          markup_set INTEGER NOT NULL DEFAULT 0,
          capture INTEGER,
          retention_days INTEGER,
          created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seats (
          seat_id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL,
          label TEXT NOT NULL,
          allowance_usd REAL,
          ceiling_usd REAL,
          daily_usd REAL,
          monthly_usd REAL,
          models_json TEXT,
          roles_json TEXT,
          created_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seats_tenant ON seats(tenant_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seat_keys (
          api_key_id TEXT PRIMARY KEY,
          seat_id TEXT NOT NULL,
          tenant_id TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seat_keys_seat ON seat_keys(seat_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seat_keys_tenant ON seat_keys(tenant_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seat_managers (
          manager_id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL,
          token_sha256 TEXT NOT NULL UNIQUE,
          display TEXT NOT NULL,
          label TEXT NOT NULL,
          created_at REAL NOT NULL,
          lifecycle TEXT NOT NULL DEFAULT 'active'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seat_managers_tenant ON seat_managers(tenant_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seat_audit (
          audit_id TEXT PRIMARY KEY,
          created_at REAL NOT NULL,
          actor TEXT NOT NULL,
          tenant_id TEXT NOT NULL,
          seat_id TEXT NOT NULL DEFAULT '',
          action TEXT NOT NULL,
          old_json TEXT NOT NULL,
          new_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_seat_audit_tenant ON seat_audit(tenant_id, created_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seat_questions (
          question_id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL,
          seat_id TEXT NOT NULL,
          api_key_id TEXT NOT NULL,
          created_at REAL NOT NULL,
          body TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_seat_questions_tenant "
        "ON seat_questions(tenant_id, created_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_counters (
          api_key_id TEXT PRIMARY KEY,
          requests INTEGER NOT NULL DEFAULT 0,
          prompt_tokens INTEGER NOT NULL DEFAULT 0,
          completion_tokens INTEGER NOT NULL DEFAULT 0,
          total_tokens INTEGER NOT NULL DEFAULT 0,
          billable_tokens INTEGER NOT NULL DEFAULT 0,
          estimated_tokens INTEGER NOT NULL DEFAULT 0,
          cache_hits INTEGER NOT NULL DEFAULT 0,
          failed_requests INTEGER NOT NULL DEFAULT 0,
          est_cost_usd REAL NOT NULL DEFAULT 0,
          unpriced_requests INTEGER NOT NULL DEFAULT 0,
          ready INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seat_spend (
          seat_id TEXT NOT NULL,
          bucket TEXT NOT NULL,
          est_cost_usd REAL NOT NULL DEFAULT 0,
          requests INTEGER NOT NULL DEFAULT 0,
          total_tokens INTEGER NOT NULL DEFAULT 0,
          billable_tokens INTEGER NOT NULL DEFAULT 0,
          unpriced_requests INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (seat_id, bucket)
        )
        """
    )


def note_recorded(conn: sqlite3.Connection, event: UsageEvent) -> None:
    """Bump counters for one ledger row. The chat path must not scan history."""
    if not event.api_key_id:
        return
    ensure_schema(conn)
    if _key_ready(conn, event.api_key_id):
        _increment_key(conn, event)
    else:
        _backfill_key(conn, event.api_key_id)
    seat_id = _seat_id_for_key(conn, event.api_key_id)
    if not seat_id:
        return
    if _seat_ready(conn, seat_id):
        _increment_seat(conn, seat_id, event)
    else:
        _backfill_seat(conn, seat_id)


def read_billable(conn: sqlite3.Connection, api_key_id: str) -> int:
    """O(1) after the first fill. Does not call ``UsageStore.summary``."""
    ensure_schema(conn)
    _ensure_key_ready(conn, api_key_id)
    row = conn.execute(
        "SELECT billable_tokens FROM usage_counters WHERE api_key_id=?",
        (api_key_id,),
    ).fetchone()
    if row is None:
        return 0
    return int(row["billable_tokens"])


def billable_for_gate(store: _BillableStore, api_key_id: str) -> int:
    """Pack credit input. Flag off keeps the all-time summary scan."""
    if not seat_plane_enabled():
        usage_now = store.summary(api_key_id=api_key_id)
        return int(usage_now.get("billable_tokens") or 0)
    try:
        return store.counter_billable(api_key_id)
    except Exception as exc:
        log.warning("usage_counter_read_failed", error_type=type(exc).__name__)
        usage_now = store.summary(api_key_id=api_key_id)
        return int(usage_now.get("billable_tokens") or 0)


def seat_block(
    api_key_id: str,
    model: str,
    role: object,
    *,
    db_path: Path | None = None,
) -> SeatBlock | None:
    """Allowlist and caps for a bound seat. Unbound keys are unchanged."""
    if not seat_plane_enabled():
        return None
    try:
        return SeatStore(db_path).gate(api_key_id, model, role)
    except Exception as exc:
        log.warning("seat_gate_failed", error_type=type(exc).__name__)
        return None


def maybe_capture_question(
    api_key_id: str,
    messages: object,
    *,
    db_path: Path | None = None,
) -> None:
    """Store the user question only when that tenant turned capture on."""
    if not seat_plane_enabled():
        return
    try:
        SeatStore(db_path).maybe_capture(api_key_id, messages)
    except Exception as exc:
        log.warning("seat_question_capture_failed", error_type=type(exc).__name__)


def manager_token_ok(token: str, *, db_path: Path | None = None) -> bool:
    if not seat_plane_enabled():
        return False
    try:
        return SeatStore(db_path).authenticate(token) is not None
    except Exception as exc:
        log.warning("seat_manager_lookup_failed", error_type=type(exc).__name__)
        return False


def scoped_metering(
    token: str,
    account_id: str,
    accounts: AccountStore,
    entitlements: EntitlementStore,
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Tenant ledger for this manager. Does not call ``usage.summary()``."""
    store = SeatStore(db_path)
    manager = store.authenticate(token)
    if manager is None:
        raise SeatError("unauthorized", 401)
    tenant = store.tenant_by_id(manager.tenant_id)
    if tenant is None or tenant["account_id"] != account_id or accounts.get(account_id) is None:
        raise SeatError("account not found", 404)
    ledger = store.tenant_ledger(manager.tenant_id)
    body = metering_overlay(entitlements.get(account_id), ledger)
    body["ledger_scope"] = "tenant"
    body["tenant_id"] = manager.tenant_id
    body["note"] = (
        "ledger is this tenant only. "
        "usage $/unit is USD NEEDS-YOU -- this overlay never invents a bill."
    )
    return body


def manager_usage(
    token: str,
    query: dict[str, str],
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    store = SeatStore(db_path)
    manager = store.authenticate(token)
    if manager is None:
        raise SeatError("unauthorized", 401)
    actor = Actor("manager", manager.tenant_id, manager.manager_id)
    return store.usage_page(actor, query)


def _key_ready(conn: sqlite3.Connection, api_key_id: str) -> bool:
    row = conn.execute(
        "SELECT ready FROM usage_counters WHERE api_key_id=?",
        (api_key_id,),
    ).fetchone()
    return row is not None and int(row["ready"]) == 1


def _ensure_key_ready(conn: sqlite3.Connection, api_key_id: str) -> None:
    if not _key_ready(conn, api_key_id):
        _backfill_key(conn, api_key_id)


def _seat_ready(conn: sqlite3.Connection, seat_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seat_spend WHERE seat_id=? AND bucket=?",
        (seat_id, _READY_BUCKET),
    ).fetchone()
    return row is not None


def _seat_id_for_key(conn: sqlite3.Connection, api_key_id: str) -> str:
    row = conn.execute(
        "SELECT seat_id FROM seat_keys WHERE api_key_id=?",
        (api_key_id,),
    ).fetchone()
    if row is None:
        return ""
    return str(row["seat_id"])


def _event_bits(event: UsageEvent) -> tuple[int, int, int, int, int, int, float, int]:
    prompt = int(event.prompt_tokens)
    completion = int(event.completion_tokens)
    total = int(event.total_tokens)
    billable = 0 if event.cache_hit else total
    estimated = total if event.estimated else 0
    failed = 1 if int(event.status) >= 400 or int(event.status) == 0 else 0
    if event.est_cost_usd is None:
        return prompt, completion, total, billable, estimated, failed, 0.0, 1
    return prompt, completion, total, billable, estimated, failed, float(event.est_cost_usd), 0


def _backfill_key(conn: sqlite3.Connection, api_key_id: str) -> None:
    row = conn.execute(
        """
        SELECT COUNT(*) AS requests,
               COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens),0) AS completion_tokens,
               COALESCE(SUM(total_tokens),0) AS total_tokens,
               COALESCE(SUM(CASE WHEN cache_hit=0 THEN total_tokens ELSE 0 END),0) AS billable,
               COALESCE(SUM(CASE WHEN estimated=1 THEN total_tokens ELSE 0 END),0) AS estimated,
               COALESCE(SUM(cache_hit),0) AS cache_hits,
               COALESCE(SUM(CASE WHEN status>=400 OR status=0 THEN 1 ELSE 0 END),0) AS failed
        FROM usage_events WHERE api_key_id=?
        """,
        (api_key_id,),
    ).fetchone()
    cost = conn.execute(
        """
        SELECT COALESCE(SUM(s.est_cost_usd),0) AS cost,
               COALESCE(SUM(CASE WHEN s.est_cost_usd IS NULL THEN 1 ELSE 0 END),0) AS unpriced
        FROM usage_events e
        LEFT JOIN route_spend s ON s.event_id = e.event_id
        WHERE e.api_key_id=?
        """,
        (api_key_id,),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO usage_counters (
          api_key_id, requests, prompt_tokens, completion_tokens, total_tokens,
          billable_tokens, estimated_tokens, cache_hits, failed_requests,
          est_cost_usd, unpriced_requests, ready
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)
        ON CONFLICT(api_key_id) DO UPDATE SET
          requests=excluded.requests,
          prompt_tokens=excluded.prompt_tokens,
          completion_tokens=excluded.completion_tokens,
          total_tokens=excluded.total_tokens,
          billable_tokens=excluded.billable_tokens,
          estimated_tokens=excluded.estimated_tokens,
          cache_hits=excluded.cache_hits,
          failed_requests=excluded.failed_requests,
          est_cost_usd=excluded.est_cost_usd,
          unpriced_requests=excluded.unpriced_requests,
          ready=1
        """,
        (
            api_key_id,
            int(row["requests"]),
            int(row["prompt_tokens"]),
            int(row["completion_tokens"]),
            int(row["total_tokens"]),
            int(row["billable"]),
            int(row["estimated"]),
            int(row["cache_hits"]),
            int(row["failed"]),
            float(cost["cost"]),
            int(cost["unpriced"]),
        ),
    )


def _increment_key(conn: sqlite3.Connection, event: UsageEvent) -> None:
    prompt, completion, total, billable, estimated, failed, cost, unpriced = _event_bits(event)
    conn.execute(
        """
        UPDATE usage_counters SET
          requests = requests + 1,
          prompt_tokens = prompt_tokens + ?,
          completion_tokens = completion_tokens + ?,
          total_tokens = total_tokens + ?,
          billable_tokens = billable_tokens + ?,
          estimated_tokens = estimated_tokens + ?,
          cache_hits = cache_hits + ?,
          failed_requests = failed_requests + ?,
          est_cost_usd = est_cost_usd + ?,
          unpriced_requests = unpriced_requests + ?
        WHERE api_key_id=? AND ready=1
        """,
        (
            prompt,
            completion,
            total,
            billable,
            estimated,
            1 if event.cache_hit else 0,
            failed,
            cost,
            unpriced,
            event.api_key_id,
        ),
    )


def _increment_seat(conn: sqlite3.Connection, seat_id: str, event: UsageEvent) -> None:
    _prompt, _completion, total, billable, _estimated, _failed, cost, unpriced = _event_bits(event)
    month, day = buckets_for(float(event.created_at))
    for bucket in (f"m:{month}", f"d:{day}", "all"):
        conn.execute(
            """
            INSERT INTO seat_spend (
              seat_id, bucket, est_cost_usd, requests, total_tokens,
              billable_tokens, unpriced_requests
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(seat_id, bucket) DO UPDATE SET
              est_cost_usd = est_cost_usd + excluded.est_cost_usd,
              requests = requests + 1,
              total_tokens = total_tokens + excluded.total_tokens,
              billable_tokens = billable_tokens + excluded.billable_tokens,
              unpriced_requests = unpriced_requests + excluded.unpriced_requests
            """,
            (seat_id, bucket, cost, 1, total, billable, unpriced),
        )


def _backfill_seat(conn: sqlite3.Connection, seat_id: str) -> None:
    conn.execute("DELETE FROM seat_spend WHERE seat_id=?", (seat_id,))
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m', e.created_at, 'unixepoch') AS month,
               strftime('%Y-%m-%d', e.created_at, 'unixepoch') AS day,
               COALESCE(SUM(s.est_cost_usd),0) AS cost,
               COUNT(*) AS requests,
               COALESCE(SUM(e.total_tokens),0) AS tokens,
               COALESCE(SUM(CASE WHEN e.cache_hit=0 THEN e.total_tokens ELSE 0 END),0) AS billable,
               COALESCE(SUM(CASE WHEN s.est_cost_usd IS NULL THEN 1 ELSE 0 END),0) AS unpriced
        FROM usage_events e
        JOIN seat_keys k ON k.api_key_id = e.api_key_id
        LEFT JOIN route_spend s ON s.event_id = e.event_id
        WHERE k.seat_id=?
        GROUP BY month, day
        """,
        (seat_id,),
    ).fetchall()
    totals = [0.0, 0, 0, 0, 0]
    for row in rows:
        cost = float(row["cost"])
        requests = int(row["requests"])
        tokens = int(row["tokens"])
        billable = int(row["billable"])
        unpriced = int(row["unpriced"])
        totals[0] += cost
        totals[1] += requests
        totals[2] += tokens
        totals[3] += billable
        totals[4] += unpriced
        if row["month"]:
            _insert_spend(
                conn, seat_id, f"m:{row['month']}", cost, requests, tokens, billable, unpriced
            )
        if row["day"]:
            _insert_spend(
                conn, seat_id, f"d:{row['day']}", cost, requests, tokens, billable, unpriced
            )
    _insert_spend(
        conn,
        seat_id,
        "all",
        totals[0],
        totals[1],
        totals[2],
        totals[3],
        totals[4],
    )
    _insert_spend(conn, seat_id, _READY_BUCKET, 0.0, 0, 0, 0, 0)


def _insert_spend(
    conn: sqlite3.Connection,
    seat_id: str,
    bucket: str,
    cost: float,
    requests: int,
    tokens: int,
    billable: int,
    unpriced: int,
) -> None:
    conn.execute(
        """
        INSERT INTO seat_spend (
          seat_id, bucket, est_cost_usd, requests, total_tokens,
          billable_tokens, unpriced_requests
        ) VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(seat_id, bucket) DO UPDATE SET
          est_cost_usd = est_cost_usd + excluded.est_cost_usd,
          requests = requests + excluded.requests,
          total_tokens = total_tokens + excluded.total_tokens,
          billable_tokens = billable_tokens + excluded.billable_tokens,
          unpriced_requests = unpriced_requests + excluded.unpriced_requests
        """,
        (seat_id, bucket, cost, requests, tokens, billable, unpriced),
    )


def _money_fields() -> dict[str, Any]:
    return {
        "priced": False,
        "currency": CURRENCY_CODE,
        "currency_sign": CURRENCY_SIGN,
        "usage_unit_usd": USAGE_UNIT_USD,
        "usage_unit_status": USAGE_UNIT_STATUS,
        "usage_unit_prefix": USAGE_UNIT_PREFIX,
        "usage_unit_sign": USAGE_UNIT_SIGN,
    }


class SeatStore:
    """Seats, managers, counters, and bills in the same keys.db. Not a second vault."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path if db_path is not None else keys_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _run(self, fn: Any) -> Any:
        conn = self._connect()
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                ensure_schema(conn)
                result = fn(conn)
                conn.execute("COMMIT")
                return result
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def authenticate(self, token: str) -> Manager | None:
        cleaned = (token or "").strip()
        digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()

        def _lookup(conn: sqlite3.Connection) -> Manager | None:
            row = conn.execute(
                "SELECT * FROM seat_managers WHERE token_sha256=?",
                (digest,),
            ).fetchone()
            if row is None or row["lifecycle"] != "active":
                hmac.compare_digest(digest, _ABSENT_DIGEST)
                return None
            if not hmac.compare_digest(digest, str(row["token_sha256"])):
                return None
            return Manager(
                manager_id=str(row["manager_id"]),
                tenant_id=str(row["tenant_id"]),
                label=str(row["label"]),
            )

        try:
            return self._run(_lookup)
        except sqlite3.Error as exc:
            log.warning("seat_manager_lookup_failed", error_type=type(exc).__name__)
            return None

    def tenant_by_id(self, tenant_id: str) -> dict[str, Any] | None:
        def _get(conn: sqlite3.Connection) -> dict[str, Any] | None:
            row = conn.execute(
                "SELECT * FROM seat_tenants WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
            if row is None:
                return None
            return _tenant_dict(row)

        return self._run(_get)

    def create_tenant(
        self, actor: Actor, account_id: str, label: str, markup: float | None
    ) -> dict[str, Any]:
        _operator_only(actor)
        cleaned = (account_id or "").strip()
        if not cleaned or len(cleaned) > 64:
            raise SeatError("account_id is required")
        name = _label(label, "label")
        markup_set = 0
        markup_value = DEFAULT_MARKUP
        if markup is not None:
            markup_value = _require_markup(markup, "markup")
            markup_set = 1

        def _insert(conn: sqlite3.Connection) -> dict[str, Any]:
            taken = conn.execute(
                "SELECT tenant_id FROM seat_tenants WHERE account_id=?",
                (cleaned,),
            ).fetchone()
            if taken is not None:
                raise SeatError("account already has a tenant")
            tenant_id = uuid.uuid4().hex[:12]
            now = time.time()
            conn.execute(
                """
                INSERT INTO seat_tenants (
                  tenant_id, account_id, label, markup, markup_set, capture,
                  retention_days, created_at
                ) VALUES (?,?,?,?,?,NULL,NULL,?)
                """,
                (tenant_id, cleaned, name, markup_value, markup_set, now),
            )
            _audit(
                conn,
                actor=actor.who,
                tenant_id=tenant_id,
                seat_id="",
                action="tenant_create",
                old={},
                new={"account_id": cleaned, "label": name, "markup": markup_value},
            )
            row = conn.execute(
                "SELECT * FROM seat_tenants WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
            return _tenant_dict(row)

        return self._run(_insert)

    def create_seat(self, actor: Actor, tenant_id: str, label: str) -> dict[str, Any]:
        name = _label(label, "label")

        def _insert(conn: sqlite3.Connection) -> dict[str, Any]:
            tenant = _owned_tenant(conn, actor, tenant_id)
            seat_id = uuid.uuid4().hex[:12]
            now = time.time()
            conn.execute(
                """
                INSERT INTO seats (
                  seat_id, tenant_id, label, allowance_usd, ceiling_usd, daily_usd,
                  monthly_usd, models_json, roles_json, created_at
                ) VALUES (?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,?)
                """,
                (seat_id, tenant, name, now),
            )
            _audit(
                conn,
                actor=actor.who,
                tenant_id=tenant,
                seat_id=seat_id,
                action="seat_create",
                old={},
                new={"label": name},
            )
            return {"seat_id": seat_id, "tenant_id": tenant, "label": name}

        return self._run(_insert)

    def bind_key(self, actor: Actor, seat_id: str, api_key_id: str) -> dict[str, Any]:
        _operator_only(actor)
        key_id = (api_key_id or "").strip()
        if not key_id:
            raise SeatError("api_key_id is required")

        def _bind(conn: sqlite3.Connection) -> dict[str, Any]:
            seat = conn.execute("SELECT * FROM seats WHERE seat_id=?", (seat_id,)).fetchone()
            if seat is None:
                raise SeatError("not found", 404)
            key = conn.execute(
                "SELECT key_id FROM api_keys WHERE key_id=?",
                (key_id,),
            ).fetchone()
            if key is None:
                raise SeatError("api key not found", 404)
            current = conn.execute(
                "SELECT seat_id, tenant_id FROM seat_keys WHERE api_key_id=?",
                (key_id,),
            ).fetchone()
            old_seat = "" if current is None else str(current["seat_id"])
            if old_seat == seat_id:
                return {"ok": True, "seat_id": seat_id, "api_key_id": key_id, "moved": False}
            conn.execute(
                """
                INSERT INTO seat_keys (api_key_id, seat_id, tenant_id) VALUES (?,?,?)
                ON CONFLICT(api_key_id) DO UPDATE SET
                  seat_id=excluded.seat_id,
                  tenant_id=excluded.tenant_id
                """,
                (key_id, seat_id, seat["tenant_id"]),
            )
            conn.execute("DELETE FROM seat_spend WHERE seat_id=?", (seat_id,))
            if old_seat:
                conn.execute("DELETE FROM seat_spend WHERE seat_id=?", (old_seat,))
            _audit(
                conn,
                actor=actor.who,
                tenant_id=str(seat["tenant_id"]),
                seat_id=seat_id,
                action="bind_key",
                old={"seat_id": old_seat, "api_key_id": key_id},
                new={"seat_id": seat_id, "api_key_id": key_id},
            )
            return {"ok": True, "seat_id": seat_id, "api_key_id": key_id, "moved": bool(old_seat)}

        return self._run(_bind)

    def issue_manager(self, actor: Actor, tenant_id: str, label: str) -> dict[str, Any]:
        _operator_only(actor)
        name = _label(label, "label")
        token = MANAGER_PREFIX + secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        display = token[:10]
        manager_id = uuid.uuid4().hex[:12]

        def _insert(conn: sqlite3.Connection) -> None:
            _owned_tenant(conn, actor, tenant_id)
            conn.execute(
                """
                INSERT INTO seat_managers (
                  manager_id, tenant_id, token_sha256, display, label, created_at, lifecycle
                ) VALUES (?,?,?,?,?,?,'active')
                """,
                (manager_id, tenant_id, digest, display, name, time.time()),
            )
            _audit(
                conn,
                actor=actor.who,
                tenant_id=tenant_id,
                seat_id="",
                action="manager_issue",
                old={},
                new={"manager_id": manager_id, "label": name, "display": display},
            )

        self._run(_insert)
        log.info("seat_manager_issued", manager_id=manager_id, tenant_id=tenant_id)
        return {
            "ok": True,
            "manager_id": manager_id,
            "tenant_id": tenant_id,
            "label": name,
            "display": display,
            "token": token,
        }

    def set_caps(
        self, actor: Actor, seat_id: str, updates: dict[str, float | None]
    ) -> dict[str, Any]:
        if not updates:
            raise SeatError("no cap fields")
        for key, value in updates.items():
            if key not in {"daily_usd", "monthly_usd", "allowance_usd", "ceiling_usd"}:
                raise SeatError("unknown cap field")
            if value is not None:
                _require_usd(value, key)

        def _update(conn: sqlite3.Connection) -> dict[str, Any]:
            seat = _seat_for_actor(conn, actor, seat_id)
            old = _cap_view(seat)
            new = dict(old)
            assignments: list[str] = []
            params: list[Any] = []
            for key, value in updates.items():
                new[key] = value
                assignments.append(f"{key}=?")
                params.append(value)
            params.append(seat_id)
            conn.execute(
                f"UPDATE seats SET {', '.join(assignments)} WHERE seat_id=?",
                params,
            )
            _audit(
                conn,
                actor=actor.who,
                tenant_id=str(seat["tenant_id"]),
                seat_id=seat_id,
                action="seat_caps",
                old=old,
                new=new,
            )
            return {"ok": True, "seat_id": seat_id, "caps": new}

        return self._run(_update)

    def set_allowlist(
        self, actor: Actor, seat_id: str, models: list[str], roles: list[str]
    ) -> dict[str, Any]:
        model_list = _name_list(models, "allowlist")
        role_list = _role_list(roles, "allowlist")

        def _update(conn: sqlite3.Connection) -> dict[str, Any]:
            seat = _seat_for_actor(conn, actor, seat_id)
            old = _allow_view(seat)
            new = {"models": model_list, "roles": role_list}
            conn.execute(
                "UPDATE seats SET models_json=?, roles_json=? WHERE seat_id=?",
                (json.dumps(model_list), json.dumps(role_list), seat_id),
            )
            _audit(
                conn,
                actor=actor.who,
                tenant_id=str(seat["tenant_id"]),
                seat_id=seat_id,
                action="seat_allowlist",
                old=old,
                new=new,
            )
            return {"ok": True, "seat_id": seat_id, "allowlist": new}

        return self._run(_update)

    def set_markup(self, actor: Actor, tenant_id: str, markup: float) -> dict[str, Any]:
        value = _require_markup(markup, "markup")

        def _update(conn: sqlite3.Connection) -> dict[str, Any]:
            row = _tenant_row(conn, _owned_tenant(conn, actor, tenant_id))
            old = {"markup": float(row["markup"]), "markup_set": int(row["markup_set"])}
            conn.execute(
                "UPDATE seat_tenants SET markup=?, markup_set=1 WHERE tenant_id=?",
                (value, row["tenant_id"]),
            )
            new = {"markup": value, "markup_set": 1}
            _audit(
                conn,
                actor=actor.who,
                tenant_id=str(row["tenant_id"]),
                seat_id="",
                action="tenant_markup",
                old=old,
                new=new,
            )
            return {"ok": True, "tenant_id": str(row["tenant_id"]), "markup": value}

        return self._run(_update)

    def set_capture(
        self, actor: Actor, tenant_id: str, enabled: bool, retention_days: int
    ) -> dict[str, Any]:
        if isinstance(retention_days, bool) or not isinstance(retention_days, int):
            raise SeatError("retention_days must be a whole number")
        if retention_days < 0 or retention_days > 3650:
            raise SeatError("retention_days must be from 0 to 3650")

        def _update(conn: sqlite3.Connection) -> dict[str, Any]:
            row = _tenant_row(conn, _owned_tenant(conn, actor, tenant_id))
            policy = current_policy().body
            old = {
                "enabled": _capture_on(row, policy),
                "retention_days": _retention(row, policy),
            }
            conn.execute(
                "UPDATE seat_tenants SET capture=?, retention_days=? WHERE tenant_id=?",
                (1 if enabled else 0, retention_days, row["tenant_id"]),
            )
            new = {"enabled": enabled, "retention_days": retention_days}
            _audit(
                conn,
                actor=actor.who,
                tenant_id=str(row["tenant_id"]),
                seat_id="",
                action="question_capture",
                old=old,
                new=new,
            )
            if not enabled or retention_days == 0:
                _purge_questions(conn, str(row["tenant_id"]), 0 if not enabled else retention_days)
            return {"ok": True, "tenant_id": str(row["tenant_id"]), **new}

        return self._run(_update)

    def list_audit(self, actor: Actor, tenant_id: str) -> dict[str, Any]:
        def _list(conn: sqlite3.Connection) -> dict[str, Any]:
            owned = _owned_tenant(conn, actor, tenant_id)
            rows = conn.execute(
                """
                SELECT * FROM seat_audit WHERE tenant_id=?
                ORDER BY created_at DESC, audit_id DESC LIMIT 100
                """,
                (owned,),
            ).fetchall()
            return {
                "ok": True,
                "tenant_id": owned,
                "audit": [_audit_dict(row) for row in rows],
            }

        return self._run(_list)

    def list_questions(self, actor: Actor, tenant_id: str) -> dict[str, Any]:
        if actor.kind != "manager":
            raise SeatError("not found", 404)

        def _list(conn: sqlite3.Connection) -> dict[str, Any]:
            owned = _owned_tenant(conn, actor, tenant_id)
            row = _tenant_row(conn, owned)
            policy = current_policy().body
            enabled = _capture_on(row, policy)
            days = _retention(row, policy)
            _purge_questions(conn, owned, 0 if not enabled else days)
            questions: list[dict[str, Any]] = []
            if enabled:
                found = conn.execute(
                    """
                    SELECT question_id, seat_id, api_key_id, created_at, body
                    FROM seat_questions WHERE tenant_id=?
                    ORDER BY created_at DESC LIMIT 200
                    """,
                    (owned,),
                ).fetchall()
                questions = [
                    {
                        "question_id": r["question_id"],
                        "seat_id": r["seat_id"],
                        "api_key_id": r["api_key_id"],
                        "created_at": float(r["created_at"]),
                        "text": r["body"],
                    }
                    for r in found
                ]
            return {
                "ok": True,
                "tenant_id": owned,
                "enabled": enabled,
                "retention_days": days,
                "questions": questions,
            }

        return self._run(_list)

    def billing(self, actor: Actor, tenant_id: str, month: str) -> dict[str, Any]:
        start, end = month_window(month)

        def _bill(conn: sqlite3.Connection) -> dict[str, Any]:
            owned = _owned_tenant(conn, actor, tenant_id)
            tenant = _tenant_row(conn, owned)
            policy = current_policy().body
            markup = _markup_for(tenant, policy)
            seats = conn.execute(
                "SELECT * FROM seats WHERE tenant_id=? ORDER BY seat_id",
                (owned,),
            ).fetchall()
            rows: list[dict[str, Any]] = []
            totals = {
                "metered_usd": 0.0,
                "overage_usd": 0.0,
                "allowance_usd": 0.0,
                "requests": 0,
                "total_tokens": 0,
                "unpriced_requests": 0,
            }
            for seat in seats:
                for key in _keys_for_seat(conn, str(seat["seat_id"])):
                    _ensure_key_ready(conn, key)
                if not _seat_ready(conn, str(seat["seat_id"])):
                    _backfill_seat(conn, str(seat["seat_id"]))
                spent = _bucket(conn, str(seat["seat_id"]), f"m:{month}")
                limits = _effective(seat, tenant, policy)
                metered = round(float(spent["est_cost_usd"]), 6)
                allowance = limits.allowance_usd
                over = overage_usd(metered, allowance, markup)
                ceiling = limits.ceiling_usd
                item = {
                    "seat_id": seat["seat_id"],
                    "label": seat["label"],
                    "metered_usd": metered,
                    "allowance_usd": allowance,
                    "markup": markup,
                    "overage_usd": over,
                    "ceiling_usd": ceiling,
                    "requests": int(spent["requests"]),
                    "total_tokens": int(spent["total_tokens"]),
                    "unpriced_requests": int(spent["unpriced_requests"]),
                }
                rows.append(item)
                totals["metered_usd"] = round(totals["metered_usd"] + metered, 6)
                totals["overage_usd"] = round(totals["overage_usd"] + over, 6)
                totals["allowance_usd"] = round(totals["allowance_usd"] + allowance, 6)
                totals["requests"] += int(spent["requests"])
                totals["total_tokens"] += int(spent["total_tokens"])
                totals["unpriced_requests"] += int(spent["unpriced_requests"])
            return {
                "ok": True,
                "tenant_id": owned,
                "month": month,
                "period_start": start,
                "period_end": end,
                "markup": markup,
                "seats": rows,
                "totals": totals,
            }

        return self._run(_bill)

    def usage_page(self, actor: Actor, query: dict[str, str]) -> dict[str, Any]:
        since = _query_float(query, "since")
        until = _query_float(query, "until")
        if since is not None and until is not None and since > until:
            raise SeatError("since must be <= until")
        limit = _query_limit(query.get("limit"))
        cursor_raw = (query.get("cursor") or "").strip()
        cursor = _decode_cursor(cursor_raw) if cursor_raw else None
        seat_filter = (query.get("seat_id") or "").strip()
        requested = (query.get("tenant_id") or "").strip()

        def _page(conn: sqlite3.Connection) -> dict[str, Any]:
            tenant_id = _owned_tenant(conn, actor, requested)
            if seat_filter:
                seat = conn.execute(
                    "SELECT seat_id FROM seats WHERE seat_id=? AND tenant_id=?",
                    (seat_filter, tenant_id),
                ).fetchone()
                if seat is None:
                    raise SeatError("not found", 404)
            events, next_cursor = _event_page(
                conn,
                tenant_id,
                since=since,
                until=until,
                seat_id=seat_filter,
                cursor=cursor,
                limit=limit,
            )
            totals = _window_totals(conn, tenant_id, since=since, until=until, seat_id=seat_filter)
            return {
                "ok": True,
                "tenant_id": tenant_id,
                "events": events,
                "count": len(events),
                "next_cursor": next_cursor,
                "since": since,
                "until": until,
                "totals": totals,
            }

        return self._run(_page)

    def tenant_ledger(self, tenant_id: str) -> dict[str, Any]:
        def _sum(conn: sqlite3.Connection) -> dict[str, Any]:
            keys = conn.execute(
                "SELECT api_key_id FROM seat_keys WHERE tenant_id=?",
                (tenant_id,),
            ).fetchall()
            for key in keys:
                _ensure_key_ready(conn, str(key["api_key_id"]))
            row = conn.execute(
                """
                SELECT COALESCE(SUM(c.requests),0) AS requests,
                       COALESCE(SUM(c.prompt_tokens),0) AS prompt_tokens,
                       COALESCE(SUM(c.completion_tokens),0) AS completion_tokens,
                       COALESCE(SUM(c.total_tokens),0) AS total_tokens,
                       COALESCE(SUM(c.billable_tokens),0) AS billable,
                       COALESCE(SUM(c.estimated_tokens),0) AS estimated,
                       COALESCE(SUM(c.cache_hits),0) AS cache_hits,
                       COALESCE(SUM(c.failed_requests),0) AS failed,
                       COALESCE(SUM(c.est_cost_usd),0) AS cost,
                       COALESCE(SUM(c.unpriced_requests),0) AS unpriced
                FROM seat_keys k
                LEFT JOIN usage_counters c ON c.api_key_id = k.api_key_id
                WHERE k.tenant_id=?
                """,
                (tenant_id,),
            ).fetchone()
            body = {
                "requests": int(row["requests"]),
                "prompt_tokens": int(row["prompt_tokens"]),
                "completion_tokens": int(row["completion_tokens"]),
                "total_tokens": int(row["total_tokens"]),
                "billable_tokens": int(row["billable"]),
                "estimated_tokens": int(row["estimated"]),
                "cache_hits": int(row["cache_hits"]),
                "failed_requests": int(row["failed"]),
                "est_cost_usd": round(float(row["cost"]), 6),
                "unpriced_requests": int(row["unpriced"]),
            }
            body.update(_money_fields())
            return body

        return self._run(_sum)

    def gate(self, api_key_id: str, model: str, role: object) -> SeatBlock | None:
        def _gate(conn: sqlite3.Connection) -> SeatBlock | None:
            seat_id = _seat_id_for_key(conn, api_key_id)
            if not seat_id:
                return None
            seat = conn.execute("SELECT * FROM seats WHERE seat_id=?", (seat_id,)).fetchone()
            if seat is None:
                return None
            tenant = _tenant_row(conn, str(seat["tenant_id"]))
            policy = current_policy().body
            limits = _effective(seat, tenant, policy)
            if limits.models and model not in limits.models:
                return SeatBlock(
                    status=403,
                    error_type="openvault_seat_allowlist",
                    message="this model is not in the seat allowlist",
                    reason="model",
                    seat_id=seat_id,
                )
            if limits.roles and (not isinstance(role, str) or role not in limits.roles):
                return SeatBlock(
                    status=403,
                    error_type="openvault_seat_allowlist",
                    message="this role is not in the seat allowlist",
                    reason="role",
                    seat_id=seat_id,
                )
            if not _seat_ready(conn, seat_id):
                _backfill_seat(conn, seat_id)
            now = time.time()
            month, day = buckets_for(now)
            month_spent = float(_bucket(conn, seat_id, f"m:{month}")["est_cost_usd"])
            day_spent = float(_bucket(conn, seat_id, f"d:{day}")["est_cost_usd"])
            checks = (
                ("ceiling_usd", limits.ceiling_usd, month_spent),
                ("monthly_usd", limits.monthly_usd, month_spent),
                ("daily_usd", limits.daily_usd, day_spent),
            )
            for name, limit, spent in checks:
                if limit is not None and spent >= limit:
                    kind = (
                        "openvault_seat_ceiling" if name == "ceiling_usd" else "openvault_seat_cap"
                    )
                    return SeatBlock(
                        status=402,
                        error_type=kind,
                        message=f"seat {name} is {limit} USD; {round(spent, 6)} spent",
                        reason=name,
                        seat_id=seat_id,
                        cap=name,
                    )
            return None

        return self._run(_gate)

    def maybe_capture(self, api_key_id: str, messages: object) -> None:
        text = question_text(messages)
        if not text:
            return

        def _store(conn: sqlite3.Connection) -> None:
            seat_id = _seat_id_for_key(conn, api_key_id)
            if not seat_id:
                return
            seat = conn.execute(
                "SELECT tenant_id FROM seats WHERE seat_id=?",
                (seat_id,),
            ).fetchone()
            if seat is None:
                return
            tenant = _tenant_row(conn, str(seat["tenant_id"]))
            policy = current_policy().body
            if not _capture_on(tenant, policy):
                return
            days = _retention(tenant, policy)
            _purge_questions(conn, str(tenant["tenant_id"]), days)
            if days <= 0:
                return
            conn.execute(
                """
                INSERT INTO seat_questions (
                  question_id, tenant_id, seat_id, api_key_id, created_at, body
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    uuid.uuid4().hex[:16],
                    tenant["tenant_id"],
                    seat_id,
                    api_key_id,
                    time.time(),
                    text,
                ),
            )

        self._run(_store)

    def policy_status(self) -> dict[str, Any]:
        state = current_policy()
        return {
            "ok": True,
            "policy_error": state.error,
            "using": state.using,
            "markup_default": DEFAULT_MARKUP,
        }


def _operator_only(actor: Actor) -> None:
    if actor.kind != "operator":
        raise SeatError("not found", 404)


def _owned_tenant(conn: sqlite3.Connection, actor: Actor, requested: str) -> str:
    if actor.kind == "manager":
        if requested and requested != actor.tenant_id:
            raise SeatError("not found", 404)
        tenant_id = actor.tenant_id or ""
    else:
        tenant_id = (requested or "").strip()
        if not tenant_id:
            raise SeatError("tenant_id is required")
    row = conn.execute(
        "SELECT tenant_id FROM seat_tenants WHERE tenant_id=?",
        (tenant_id,),
    ).fetchone()
    if row is None:
        raise SeatError("not found", 404)
    return tenant_id


def _tenant_row(conn: sqlite3.Connection, tenant_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM seat_tenants WHERE tenant_id=?",
        (tenant_id,),
    ).fetchone()
    if row is None:
        raise SeatError("not found", 404)
    return row


def _seat_for_actor(conn: sqlite3.Connection, actor: Actor, seat_id: str) -> sqlite3.Row:
    seat = conn.execute("SELECT * FROM seats WHERE seat_id=?", (seat_id,)).fetchone()
    if seat is None:
        raise SeatError("not found", 404)
    _owned_tenant(conn, actor, str(seat["tenant_id"]))
    return seat


def _tenant_dict(row: sqlite3.Row) -> dict[str, Any]:
    policy = current_policy().body
    return {
        "tenant_id": row["tenant_id"],
        "account_id": row["account_id"],
        "label": row["label"],
        "markup": _markup_for(row, policy),
        "question_capture": _capture_on(row, policy),
        "retention_days": _retention(row, policy),
        "created_at": float(row["created_at"]),
    }


def _file_seat(policy: dict[str, Any], seat_id: str) -> dict[str, Any]:
    seats = policy.get("seats")
    if not isinstance(seats, dict):
        return {}
    raw = seats.get(seat_id)
    if not isinstance(raw, dict):
        return {}
    return raw


def _file_tenant(policy: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    tenants = policy.get("tenants")
    if not isinstance(tenants, dict):
        return {}
    raw = tenants.get(tenant_id)
    if not isinstance(raw, dict):
        return {}
    return raw


def _markup_for(row: sqlite3.Row, policy: dict[str, Any]) -> float:
    if int(row["markup_set"]):
        return float(row["markup"])
    file_tenant = _file_tenant(policy, str(row["tenant_id"]))
    if "markup" in file_tenant:
        return float(file_tenant["markup"])
    if "markup" in policy:
        return float(policy["markup"])
    return DEFAULT_MARKUP


def _capture_on(row: sqlite3.Row, policy: dict[str, Any]) -> bool:
    if row["capture"] is not None:
        return int(row["capture"]) == 1
    file_tenant = _file_tenant(policy, str(row["tenant_id"]))
    if "question_capture" in file_tenant:
        return bool(file_tenant["question_capture"])
    return False


def _retention(row: sqlite3.Row, policy: dict[str, Any]) -> int:
    if row["retention_days"] is not None:
        return int(row["retention_days"])
    file_tenant = _file_tenant(policy, str(row["tenant_id"]))
    if "retention_days" in file_tenant:
        return int(file_tenant["retention_days"])
    return DEFAULT_RETENTION_DAYS


def _optional_number(db_value: object, file_seat: dict[str, Any], key: str) -> float | None:
    if db_value is not None:
        return float(db_value)  # type: ignore[arg-type]
    if key in file_seat:
        return float(file_seat[key])
    return None


def _json_list(raw: object) -> list[str] | None:
    if raw is None:
        return None
    data = json.loads(str(raw))
    if not isinstance(data, list):
        return None
    return [str(item) for item in data]


def _effective(seat: sqlite3.Row, tenant: sqlite3.Row, policy: dict[str, Any]) -> Effective:
    file_seat = _file_seat(policy, str(seat["seat_id"]))
    allowance = _optional_number(seat["allowance_usd"], file_seat, "allowance_usd")
    models = _json_list(seat["models_json"])
    if models is None and "models" in file_seat:
        models = list(file_seat["models"])
    roles = _json_list(seat["roles_json"])
    if roles is None and "roles" in file_seat:
        roles = list(file_seat["roles"])
    return Effective(
        allowance_usd=0.0 if allowance is None else allowance,
        ceiling_usd=_optional_number(seat["ceiling_usd"], file_seat, "ceiling_usd"),
        daily_usd=_optional_number(seat["daily_usd"], file_seat, "daily_usd"),
        monthly_usd=_optional_number(seat["monthly_usd"], file_seat, "monthly_usd"),
        models=models,
        roles=roles,
        markup=_markup_for(tenant, policy),
        capture=_capture_on(tenant, policy),
        retention_days=_retention(tenant, policy),
    )


def _cap_view(seat: sqlite3.Row) -> dict[str, float | None]:
    return {
        "daily_usd": None if seat["daily_usd"] is None else float(seat["daily_usd"]),
        "monthly_usd": None if seat["monthly_usd"] is None else float(seat["monthly_usd"]),
        "allowance_usd": None if seat["allowance_usd"] is None else float(seat["allowance_usd"]),
        "ceiling_usd": None if seat["ceiling_usd"] is None else float(seat["ceiling_usd"]),
    }


def _allow_view(seat: sqlite3.Row) -> dict[str, list[str] | None]:
    return {"models": _json_list(seat["models_json"]), "roles": _json_list(seat["roles_json"])}


def _audit(
    conn: sqlite3.Connection,
    *,
    actor: str,
    tenant_id: str,
    seat_id: str,
    action: str,
    old: dict[str, Any],
    new: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO seat_audit (
          audit_id, created_at, actor, tenant_id, seat_id, action, old_json, new_json
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            uuid.uuid4().hex[:16],
            time.time(),
            actor,
            tenant_id,
            seat_id,
            action,
            json.dumps(old, sort_keys=True),
            json.dumps(new, sort_keys=True),
        ),
    )


def _audit_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "audit_id": row["audit_id"],
        "created_at": float(row["created_at"]),
        "actor": row["actor"],
        "tenant_id": row["tenant_id"],
        "seat_id": row["seat_id"],
        "action": row["action"],
        "old": json.loads(row["old_json"]),
        "new": json.loads(row["new_json"]),
    }


def _purge_questions(conn: sqlite3.Connection, tenant_id: str, retention_days: int) -> None:
    if retention_days <= 0:
        conn.execute("DELETE FROM seat_questions WHERE tenant_id=?", (tenant_id,))
        return
    cutoff = time.time() - (retention_days * 86400)
    conn.execute(
        "DELETE FROM seat_questions WHERE tenant_id=? AND created_at < ?",
        (tenant_id, cutoff),
    )


def _keys_for_seat(conn: sqlite3.Connection, seat_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT api_key_id FROM seat_keys WHERE seat_id=?",
        (seat_id,),
    ).fetchall()
    return [str(row["api_key_id"]) for row in rows]


def _bucket(conn: sqlite3.Connection, seat_id: str, bucket: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM seat_spend WHERE seat_id=? AND bucket=?",
        (seat_id, bucket),
    ).fetchone()
    if row is not None:
        return row
    return {
        "est_cost_usd": 0.0,
        "requests": 0,
        "total_tokens": 0,
        "billable_tokens": 0,
        "unpriced_requests": 0,
    }  # type: ignore[return-value]


def _query_float(query: dict[str, str], name: str) -> float | None:
    raw = query.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise SeatError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise SeatError(f"{name} must be a number")
    return value


def _query_limit(raw: str | None) -> int:
    if raw is None or raw == "":
        return 50
    try:
        value = int(raw)
    except ValueError as exc:
        raise SeatError("limit must be a whole number") from exc
    if value < 1 or value > 200:
        raise SeatError("limit must be from 1 to 200")
    return value


def _decode_cursor(raw: str) -> tuple[float, str]:
    if "|" not in raw:
        raise SeatError("cursor is not valid")
    left, right = raw.split("|", 1)
    try:
        created = float(left)
    except ValueError as exc:
        raise SeatError("cursor is not valid") from exc
    if not right or not math.isfinite(created):
        raise SeatError("cursor is not valid")
    return created, right


def _encode_cursor(created_at: float, event_id: str) -> str:
    return f"{created_at:.17g}|{event_id}"


def _event_filters(
    tenant_id: str,
    *,
    since: float | None,
    until: float | None,
    seat_id: str,
    cursor: tuple[float, str] | None,
) -> tuple[str, list[Any]]:
    where = ["k.tenant_id=?"]
    params: list[Any] = [tenant_id]
    if since is not None:
        where.append("e.created_at >= ?")
        params.append(since)
    if until is not None:
        where.append("e.created_at < ?")
        params.append(until)
    if seat_id:
        where.append("k.seat_id=?")
        params.append(seat_id)
    if cursor is not None:
        where.append("(e.created_at > ? OR (e.created_at = ? AND e.event_id > ?))")
        params.extend([cursor[0], cursor[0], cursor[1]])
    return " AND ".join(where), params


def _event_page(
    conn: sqlite3.Connection,
    tenant_id: str,
    *,
    since: float | None,
    until: float | None,
    seat_id: str,
    cursor: tuple[float, str] | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    where, params = _event_filters(
        tenant_id, since=since, until=until, seat_id=seat_id, cursor=cursor
    )
    params.append(limit + 1)
    rows = conn.execute(
        f"""
        SELECT e.event_id, e.created_at, e.api_key_id, e.identity, e.model_requested,
               e.model_served, e.provider, e.prompt_tokens, e.completion_tokens,
               e.total_tokens, e.status, e.cache_hit, e.estimated, e.vault_key_id,
               k.seat_id, s.route_role, s.est_cost_usd
        FROM usage_events e
        JOIN seat_keys k ON k.api_key_id = e.api_key_id
        LEFT JOIN route_spend s ON s.event_id = e.event_id
        WHERE {where}
        ORDER BY e.created_at ASC, e.event_id ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = _encode_cursor(float(last["created_at"]), str(last["event_id"]))
    return [_event_dict(row) for row in page], next_cursor


def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
    cost = row["est_cost_usd"]
    return {
        "event_id": row["event_id"],
        "created_at": float(row["created_at"]),
        "seat_id": row["seat_id"],
        "api_key_id": row["api_key_id"],
        "identity": row["identity"],
        "model_requested": row["model_requested"],
        "model_served": row["model_served"],
        "provider": row["provider"],
        "vault_key_id": row["vault_key_id"],
        "route_role": row["route_role"] or "",
        "est_cost_usd": None if cost is None else float(cost),
        "prompt_tokens": int(row["prompt_tokens"]),
        "completion_tokens": int(row["completion_tokens"]),
        "total_tokens": int(row["total_tokens"]),
        "status": int(row["status"]),
        "cache_hit": bool(row["cache_hit"]),
        "estimated": bool(row["estimated"]),
    }


def _window_totals(
    conn: sqlite3.Connection,
    tenant_id: str,
    *,
    since: float | None,
    until: float | None,
    seat_id: str,
) -> dict[str, Any]:
    where, params = _event_filters(
        tenant_id, since=since, until=until, seat_id=seat_id, cursor=None
    )
    rows = conn.execute(
        f"""
        SELECT k.seat_id AS seat_id,
               COUNT(*) AS requests,
               COALESCE(SUM(e.total_tokens),0) AS total_tokens,
               COALESCE(SUM(CASE WHEN e.cache_hit=0 THEN e.total_tokens ELSE 0 END),0) AS billable,
               COALESCE(SUM(s.est_cost_usd),0) AS cost,
               COALESCE(SUM(CASE WHEN s.est_cost_usd IS NULL THEN 1 ELSE 0 END),0) AS unpriced
        FROM usage_events e
        JOIN seat_keys k ON k.api_key_id = e.api_key_id
        LEFT JOIN route_spend s ON s.event_id = e.event_id
        WHERE {where}
        GROUP BY k.seat_id
        ORDER BY k.seat_id
        """,
        params,
    ).fetchall()
    seats: list[dict[str, Any]] = []
    tenant = {
        "requests": 0,
        "total_tokens": 0,
        "billable_tokens": 0,
        "est_cost_usd": 0.0,
        "unpriced_requests": 0,
    }
    for row in rows:
        item = {
            "seat_id": row["seat_id"],
            "requests": int(row["requests"]),
            "total_tokens": int(row["total_tokens"]),
            "billable_tokens": int(row["billable"]),
            "est_cost_usd": round(float(row["cost"]), 6),
            "unpriced_requests": int(row["unpriced"]),
        }
        seats.append(item)
        tenant["requests"] += item["requests"]
        tenant["total_tokens"] += item["total_tokens"]
        tenant["billable_tokens"] += item["billable_tokens"]
        tenant["est_cost_usd"] = round(tenant["est_cost_usd"] + item["est_cost_usd"], 6)
        tenant["unpriced_requests"] += item["unpriced_requests"]
    return {"tenant": tenant, "seats": seats}
