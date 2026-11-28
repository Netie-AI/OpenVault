"""Usage ledger — one durable row per FreeRoute request.

The numbers already existed and were thrown away. ``usage_total_tokens`` on the
JSON path and ``SseUsageCapture`` on the stream path both feed
``limiter.settle`` purely to refund a token bucket whose Redis keys expire in a
day. Nothing survived. That means the founder could not show a user what they
used, could not price anything, and could not prove a cache ever saved a token.

Two rules this table exists to enforce:

* **Attribute the hop that actually served.** ``resolve_model`` rewrites the
  model per hop, and the fallback walk may spend the third key in the pool. A
  row that recorded the caller's requested ``"auto"``, or the first candidate,
  would be a lie with a number next to it.
* **Never invent a token count.** A stream without ``include_usage`` gives us
  no usage frame. That row is written with ``estimated=1`` and the reservation
  value, and every read path surfaces the flag — an estimate that reads as
  measured is the silent-fallback failure of R-0011 wearing a billing hat.

Same ``keys.db`` as the vault and the trust store, for one backup surface. A
billing row is never pruned the way ``health_store`` prunes heartbeats.

``route_spend`` sits beside ``usage_events`` with the same ``event_id``: the
route policy's budget scope, role and estimated USD. Hard caps sum
``route_spend.est_cost_usd``. A row whose ``cost_status`` is ``cost_unknown``
stores SQL NULL in that column, so the sum does not treat a missing price as
zero. Readers see the string ``cost_unknown``. Spend caps do not read
``limited_by``.

``limited_by`` is an additive column on ``usage_events`` (``request`` or
``token`` when the token-budget limiter refused the call, otherwise empty).
An older database gains it on open. It is not a price and not a spend cap.

``served_downgraded``, ``downgraded_from``, and ``downgraded_to`` are additive
on both tables. They are empty unless a budget refusal was skipped and a later
hop served. ``cost_status`` is additive on both tables. Older databases gain
the columns on open. None of them hold a key or a secret.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openmw.openvault.paths import keys_db_path
from openmw.openvault.vault.route_policy import COST_UNKNOWN

#: Usage $/unit is a founder decision. A number here would read as a real rate.
#: Display SKUs and this ledger still label the currency as USD (prefix + sign).
CURRENCY_CODE = "USD"
CURRENCY_SIGN = "$"
USAGE_UNIT_USD: float | None = None
USAGE_UNIT_STATUS = "NEEDS-YOU"
USAGE_UNIT_PREFIX = f"{CURRENCY_CODE} {USAGE_UNIT_STATUS}"
USAGE_UNIT_SIGN = f"{CURRENCY_SIGN} {USAGE_UNIT_STATUS}"

#: Limiter denial axis stored on a usage row. Anything else is dropped.
_LIMITED_BY_VALUES = frozenset({"", "request", "token"})


def _limited_by_value(value: str) -> str:
    """Keep ``request`` or ``token``. Drop any other text."""
    clean = value.strip()
    if clean in _LIMITED_BY_VALUES:
        return clean
    return ""


def _cost_status_value(value: str) -> str:
    """Keep ``cost_unknown``. Drop any other text so the column stays a token."""
    return COST_UNKNOWN if value.strip() == COST_UNKNOWN else ""


def _ledger_text(value: str) -> str:
    """Hop label for the ledger. No newlines, and short enough for a header."""
    return value.replace("\r", "").replace("\n", "")[:200]


def _add_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _shown_cost(raw: object, status: str) -> float | None | str:
    if status == COST_UNKNOWN:
        return COST_UNKNOWN
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return float(raw)


def _spend_row(row: sqlite3.Row) -> dict[str, Any]:
    status = str(row["cost_status"] or "")
    return {
        "event_id": row["event_id"],
        "created_at": float(row["created_at"]),
        "service_id": row["service_id"],
        "vault_key_id": row["vault_key_id"],
        "route_role": row["route_role"],
        "est_cost_usd": _shown_cost(row["est_cost_usd"], status),
        "cost_status": status,
        "served_downgraded": bool(row["served_downgraded"]),
        "downgraded_from": str(row["downgraded_from"] or ""),
        "downgraded_to": str(row["downgraded_to"] or ""),
    }


def usd_prefix(amount: int) -> str:
    """Founder display copy: USD10, USD100, USD500. Never a usage unit price."""
    return f"{CURRENCY_CODE}{amount}"


def usd_sign(amount: int) -> str:
    """Founder USD sign copy: $10, $100, $500. Never a usage unit price."""
    return f"{CURRENCY_SIGN}{amount}"


@dataclass
class UsageEvent:
    """What one gateway request cost, and who to attribute it to."""

    #: Caller identity: an issued key_id, or a loopback host for dev traffic.
    identity: str
    tier: str
    #: Set when the caller presented an issued key. None for loopback dev calls.
    api_key_id: str | None = None
    #: What the caller asked for, verbatim — often "auto".
    model_requested: str = ""
    #: What actually served. Empty when nothing did.
    model_served: str = ""
    provider: str = ""
    #: The vault key that spent. Attribution for provider-side reconciliation.
    vault_key_id: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    #: True when total_tokens is a reservation, not a measured usage frame.
    estimated: bool = False
    #: True when the gateway answered without an upstream call.
    cache_hit: bool = False
    stream: bool = False
    status: int = 0
    #: Typed refusal name when the request failed, e.g. openvault_vault_sealed.
    error_type: str = ""
    #: ``request`` or ``token`` when the limiter returned 429. Empty otherwise.
    limited_by: str = ""
    latency_ms: int = 0
    #: Budget scope for caps: the issued key id, or a loopback ``service_id``.
    service_id: str = ""
    #: ``sql`` | ``tool`` | ``reason`` | ``summarize`` | ``grain`` | ``ontology``,
    #: or "" when not role-routed.
    route_role: str = ""
    #: Numeric cost. None when unknown or when ``cost_status`` is ``cost_unknown``.
    est_cost_usd: float | None = 0.0
    #: ``cost_unknown`` when the price must not be read as null or zero.
    cost_status: str = ""
    served_downgraded: bool = False
    downgraded_from: str = ""
    downgraded_to: str = ""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def billable_tokens(self) -> int:
        """Tokens a bill could be computed from. A cache hit spent nothing."""
        return 0 if self.cache_hit else self.total_tokens


@dataclass
class HopTrace:
    """Filled in by the proxy so the ledger can name the hop that served.

    An out-parameter rather than a wider return type: ``chat_completions`` and
    ``prepare_chat_stream`` are called from the app and from four test modules
    that unpack ``(status, payload)``. Widening the tuple would have rewritten
    every one of those call sites to record a number they do not use.
    """

    provider: str = ""
    model_served: str = ""
    vault_key_id: str = ""
    attempts: int = 0
    cache_hit: bool = False
    error_type: str = ""
    served_local: bool = False
    #: Set when a strict pin is parked. The gateway copies this to Retry-After.
    retry_after_s: int | None = None
    served_downgraded: bool = False
    downgraded_from: str = ""
    downgraded_to: str = ""

    def note_attempt(self) -> None:
        self.attempts += 1

    def note_served(
        self, *, provider: str, model: str, vault_key_id: str, served_local: bool = False
    ) -> None:
        self.provider = provider
        self.model_served = model
        self.vault_key_id = vault_key_id
        self.served_local = served_local

    def note_downgrade(self, *, downgraded_from: str, downgraded_to: str) -> None:
        self.served_downgraded = True
        self.downgraded_from = downgraded_from
        self.downgraded_to = downgraded_to


class UsageStore:
    """Append-only record of what each caller spent."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path if db_path is not None else keys_db_path()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                  event_id TEXT PRIMARY KEY,
                  created_at REAL NOT NULL,
                  identity TEXT NOT NULL,
                  tier TEXT NOT NULL,
                  api_key_id TEXT,
                  model_requested TEXT NOT NULL DEFAULT '',
                  model_served TEXT NOT NULL DEFAULT '',
                  provider TEXT NOT NULL DEFAULT '',
                  vault_key_id TEXT NOT NULL DEFAULT '',
                  prompt_tokens INTEGER NOT NULL DEFAULT 0,
                  completion_tokens INTEGER NOT NULL DEFAULT 0,
                  total_tokens INTEGER NOT NULL DEFAULT 0,
                  estimated INTEGER NOT NULL DEFAULT 0,
                  cache_hit INTEGER NOT NULL DEFAULT 0,
                  stream INTEGER NOT NULL DEFAULT 0,
                  status INTEGER NOT NULL DEFAULT 0,
                  error_type TEXT NOT NULL DEFAULT '',
                  latency_ms INTEGER NOT NULL DEFAULT 0,
                  limited_by TEXT NOT NULL DEFAULT '',
                  served_downgraded INTEGER NOT NULL DEFAULT 0,
                  downgraded_from TEXT NOT NULL DEFAULT '',
                  downgraded_to TEXT NOT NULL DEFAULT '',
                  cost_status TEXT NOT NULL DEFAULT ''
                )
                """
            )
            usage_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(usage_events)").fetchall()
            }
            if "limited_by" not in usage_cols:
                # Additive. Empty on every row that predates the column.
                conn.execute(
                    "ALTER TABLE usage_events ADD COLUMN limited_by TEXT NOT NULL DEFAULT ''"
                )
            _add_column(
                conn,
                "usage_events",
                "served_downgraded",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _add_column(conn, "usage_events", "downgraded_from", "TEXT NOT NULL DEFAULT ''")
            _add_column(conn, "usage_events", "downgraded_to", "TEXT NOT NULL DEFAULT ''")
            _add_column(conn, "usage_events", "cost_status", "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_key_time "
                "ON usage_events(api_key_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_identity_time "
                "ON usage_events(identity, created_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS route_spend (
                  event_id TEXT PRIMARY KEY,
                  created_at REAL NOT NULL,
                  service_id TEXT NOT NULL DEFAULT '',
                  vault_key_id TEXT NOT NULL DEFAULT '',
                  route_role TEXT NOT NULL DEFAULT '',
                  est_cost_usd REAL,
                  cost_status TEXT NOT NULL DEFAULT '',
                  served_downgraded INTEGER NOT NULL DEFAULT 0,
                  downgraded_from TEXT NOT NULL DEFAULT '',
                  downgraded_to TEXT NOT NULL DEFAULT ''
                )
                """
            )
            _add_column(conn, "route_spend", "cost_status", "TEXT NOT NULL DEFAULT ''")
            _add_column(conn, "route_spend", "served_downgraded", "INTEGER NOT NULL DEFAULT 0")
            _add_column(conn, "route_spend", "downgraded_from", "TEXT NOT NULL DEFAULT ''")
            _add_column(conn, "route_spend", "downgraded_to", "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spend_service_time "
                "ON route_spend(service_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spend_key_time "
                "ON route_spend(vault_key_id, created_at)"
            )
            conn.commit()

    def record(self, event: UsageEvent) -> UsageEvent:
        with contextlib.closing(self._connect()) as conn, conn:
            cost_status = _cost_status_value(event.cost_status)
            # Unknown price stays NULL so the cap sum does not count it as $0.
            if cost_status == COST_UNKNOWN or event.est_cost_usd is None:
                stored_cost = None
            else:
                stored_cost = float(event.est_cost_usd)
            downgraded_from = _ledger_text(event.downgraded_from)
            downgraded_to = _ledger_text(event.downgraded_to)
            conn.execute(
                "INSERT INTO usage_events (event_id, created_at, identity, tier, api_key_id, "
                "model_requested, model_served, provider, vault_key_id, prompt_tokens, "
                "completion_tokens, total_tokens, estimated, cache_hit, stream, status, "
                "error_type, latency_ms, limited_by, served_downgraded, downgraded_from, "
                "downgraded_to, cost_status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.created_at,
                    event.identity,
                    event.tier,
                    event.api_key_id,
                    event.model_requested,
                    event.model_served,
                    event.provider,
                    event.vault_key_id,
                    int(event.prompt_tokens),
                    int(event.completion_tokens),
                    int(event.total_tokens),
                    1 if event.estimated else 0,
                    1 if event.cache_hit else 0,
                    1 if event.stream else 0,
                    int(event.status),
                    event.error_type,
                    int(event.latency_ms),
                    _limited_by_value(event.limited_by),
                    1 if event.served_downgraded else 0,
                    downgraded_from,
                    downgraded_to,
                    cost_status,
                ),
            )
            conn.execute(
                "INSERT INTO route_spend (event_id, created_at, service_id, vault_key_id, "
                "route_role, est_cost_usd, cost_status, served_downgraded, downgraded_from, "
                "downgraded_to) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.created_at,
                    event.service_id,
                    event.vault_key_id,
                    event.route_role,
                    stored_cost,
                    cost_status,
                    1 if event.served_downgraded else 0,
                    downgraded_from,
                    downgraded_to,
                ),
            )
            conn.commit()
        return event

    def spend_usd(
        self,
        *,
        vault_key_id: str | None = None,
        service_id: str | None = None,
        since: float,
    ) -> float:
        """Estimated USD since ``since`` for one vault key or one caller."""
        if vault_key_id:
            column, value = "vault_key_id", vault_key_id
        elif service_id:
            column, value = "service_id", service_id
        else:
            return 0.0
        with contextlib.closing(self._connect()) as conn, conn:
            row = conn.execute(
                f"SELECT COALESCE(SUM(est_cost_usd),0) AS usd FROM route_spend "
                f"WHERE {column}=? AND created_at >= ?",
                (value, float(since)),
            ).fetchone()
        return float(row["usd"])

    def spend_events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Budget rows, oldest first.

        ``est_cost_usd`` is ``cost_unknown`` when the price was missing, None
        for a legacy unpriced row with no status, otherwise a float.
        """
        with contextlib.closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM route_spend ORDER BY created_at ASC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [_spend_row(r) for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "created_at": float(row["created_at"]),
            "identity": row["identity"],
            "tier": row["tier"],
            "api_key_id": row["api_key_id"],
            "model_requested": row["model_requested"],
            "model_served": row["model_served"],
            "provider": row["provider"],
            "vault_key_id": row["vault_key_id"],
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "total_tokens": int(row["total_tokens"]),
            "estimated": bool(row["estimated"]),
            "cache_hit": bool(row["cache_hit"]),
            "stream": bool(row["stream"]),
            "status": int(row["status"]),
            "error_type": row["error_type"],
            "latency_ms": int(row["latency_ms"]),
            "limited_by": _limited_by_value(str(row["limited_by"] or "")),
            "served_downgraded": bool(row["served_downgraded"]),
            "downgraded_from": str(row["downgraded_from"] or ""),
            "downgraded_to": str(row["downgraded_to"] or ""),
            "cost_status": str(row["cost_status"] or ""),
            "billable_tokens": 0 if row["cache_hit"] else int(row["total_tokens"]),
        }

    def events(
        self,
        *,
        api_key_id: str | None = None,
        identity: str | None = None,
        since: float | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM usage_events"
        where: list[str] = []
        params: list[Any] = []
        if api_key_id:
            where.append("api_key_id=?")
            params.append(api_key_id)
        if identity:
            where.append("identity=?")
            params.append(identity)
        if since is not None:
            where.append("created_at >= ?")
            params.append(float(since))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at ASC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with contextlib.closing(self._connect()) as conn, conn:
            return [self._row(r) for r in conn.execute(sql, params).fetchall()]

    def summary(
        self,
        *,
        api_key_id: str | None = None,
        identity: str | None = None,
        since: float | None = None,
    ) -> dict[str, Any]:
        """Totals a bill could be computed from, with the honesty flags kept.

        ``estimated_tokens`` is reported separately rather than folded into the
        total: a number that mixes measured and guessed tokens without saying so
        is exactly the kind of figure nobody should invoice against.
        """
        sql = (
            "SELECT COUNT(*) AS requests, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
            "COALESCE(SUM(total_tokens),0) AS total_tokens, "
            "COALESCE(SUM(CASE WHEN cache_hit=0 THEN total_tokens ELSE 0 END),0) AS billable, "
            "COALESCE(SUM(CASE WHEN estimated=1 THEN total_tokens ELSE 0 END),0) AS estimated, "
            "COALESCE(SUM(cache_hit),0) AS cache_hits, "
            "COALESCE(SUM(CASE WHEN status>=400 OR status=0 THEN 1 ELSE 0 END),0) AS failed "
            "FROM usage_events"
        )
        where: list[str] = []
        params: list[Any] = []
        if api_key_id:
            where.append("api_key_id=?")
            params.append(api_key_id)
        if identity:
            where.append("identity=?")
            params.append(identity)
        if since is not None:
            where.append("created_at >= ?")
            params.append(float(since))
        if where:
            sql += " WHERE " + " AND ".join(where)
        with contextlib.closing(self._connect()) as conn, conn:
            row = conn.execute(sql, params).fetchone()
        return {
            "requests": int(row["requests"]),
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "total_tokens": int(row["total_tokens"]),
            "billable_tokens": int(row["billable"]),
            "estimated_tokens": int(row["estimated"]),
            "cache_hits": int(row["cache_hits"]),
            "failed_requests": int(row["failed"]),
            # No price. Display SKUs live on the system control plane; usage
            # $/unit stays NEEDS-YOU so a number here cannot look measured.
            # Experience packs (DR-0016) price estimated spend in route_packs,
            # not in this ledger.
            "priced": False,
            "currency": CURRENCY_CODE,
            "currency_sign": CURRENCY_SIGN,
            "usage_unit_usd": USAGE_UNIT_USD,
            "usage_unit_status": USAGE_UNIT_STATUS,
            "usage_unit_prefix": USAGE_UNIT_PREFIX,
            "usage_unit_sign": USAGE_UNIT_SIGN,
        }


__all__ = [
    "CURRENCY_CODE",
    "CURRENCY_SIGN",
    "USAGE_UNIT_PREFIX",
    "USAGE_UNIT_SIGN",
    "USAGE_UNIT_STATUS",
    "USAGE_UNIT_USD",
    "HopTrace",
    "UsageEvent",
    "UsageStore",
    "usd_prefix",
    "usd_sign",
]
