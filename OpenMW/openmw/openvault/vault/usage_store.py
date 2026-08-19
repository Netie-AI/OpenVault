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
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openmw.openvault.paths import keys_db_path


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
    latency_ms: int = 0
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

    def note_attempt(self) -> None:
        self.attempts += 1

    def note_served(self, *, provider: str, model: str, vault_key_id: str) -> None:
        self.provider = provider
        self.model_served = model
        self.vault_key_id = vault_key_id


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
        with self._connect() as conn:
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
                  latency_ms INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_key_time "
                "ON usage_events(api_key_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_identity_time "
                "ON usage_events(identity, created_at)"
            )
            conn.commit()

    def record(self, event: UsageEvent) -> UsageEvent:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO usage_events (event_id, created_at, identity, tier, api_key_id, "
                "model_requested, model_served, provider, vault_key_id, prompt_tokens, "
                "completion_tokens, total_tokens, estimated, cache_hit, stream, status, "
                "error_type, latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
            conn.commit()
        return event

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
        with self._connect() as conn:
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
        with self._connect() as conn:
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
            # No price. Pricing is a founder decision (STATUS.md NEEDS-YOU), and
            # a rate invented here would be indistinguishable from a real one.
            "priced": False,
        }


__all__ = ["HopTrace", "UsageEvent", "UsageStore"]
