"""Persist precheck probe history beside (in) keys.db.

Storage choice: same SQLite file as :class:`~openmw.openvault.vault.store.KeyVault`
(``OPENVAULT_HOME/keys.db``). One backup/restore surface; same pattern as
trust signing tables. Override with an explicit ``db_path`` in tests.

Retention (enforced on every write): drop rows older than 7 days, then cap at
``MAX_ROWS_PER_KEY`` (2000) newest rows per key.

Write policy: insert on status transition, else at most one row per
``HEARTBEAT_INTERVAL_S`` (15 minutes) per key.
"""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from openmw.openvault.paths import keys_db_path

HistoryStatus = Literal["ok", "auth_fail", "rate_limit", "error", "timeout"]
_VALID_STATUSES = frozenset({"ok", "auth_fail", "rate_limit", "error", "timeout"})

RETENTION_DAYS = 7
MAX_ROWS_PER_KEY = 2000
HEARTBEAT_INTERVAL_S = 15 * 60
# Statuses that count as "up" for uptime_pct (rate_limit is busy, not down).
_UP_STATUSES = frozenset({"ok", "rate_limit"})


@dataclass(frozen=True)
class HistorySample:
    t: float
    status: HistoryStatus
    latency_ms: float | None
    error: str | None = None


@dataclass(frozen=True)
class HealthSummary:
    key_id: str
    window: str
    samples: list[HistorySample]
    uptime_pct: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    current_status: HistoryStatus | None
    last_change_at: float | None
    rate_limit_count: int


def parse_window(window: str) -> float:
    """Parse ``24h`` / ``7d`` / bare hours into a duration in seconds."""
    raw = window.strip().lower()
    if not raw:
        raise ValueError("window must be non-empty")
    if raw.endswith("h") and raw[:-1].replace(".", "", 1).isdigit():
        hours = float(raw[:-1])
        if hours <= 0:
            raise ValueError("window must be positive")
        return hours * 3600.0
    if raw.endswith("d") and raw[:-1].replace(".", "", 1).isdigit():
        days = float(raw[:-1])
        if days <= 0:
            raise ValueError("window must be positive")
        return days * 86400.0
    if raw.replace(".", "", 1).isdigit():
        hours = float(raw)
        if hours <= 0:
            raise ValueError("window must be positive")
        return hours * 3600.0
    raise ValueError(f"unrecognised window {window!r}; expected e.g. 24h or 7d")


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_vals[lo]
    frac = rank - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


class HealthStore:
    """SQLite history for key precheck probes (table in keys.db)."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path if db_path is not None else keys_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS precheck_history (
                  key_id      TEXT NOT NULL,
                  checked_at  REAL NOT NULL,
                  status      TEXT NOT NULL,
                  latency_ms  REAL,
                  error       TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_precheck_key_time
                  ON precheck_history (key_id, checked_at DESC)
                """
            )
            conn.commit()

    def _latest_row(self, conn: sqlite3.Connection, key_id: str) -> tuple[float, str] | None:
        row = conn.execute(
            """
            SELECT checked_at, status FROM precheck_history
            WHERE key_id = ?
            ORDER BY checked_at DESC
            LIMIT 1
            """,
            (key_id,),
        ).fetchone()
        if row is None:
            return None
        return float(row["checked_at"]), str(row["status"])

    def prune(self, key_id: str, *, now: float | None = None) -> None:
        """Drop rows older than 7 days, then cap at MAX_ROWS_PER_KEY newest."""
        ts = time.time() if now is None else now
        cutoff = ts - RETENTION_DAYS * 86400.0
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM precheck_history WHERE key_id = ? AND checked_at < ?",
                (key_id, cutoff),
            )
            count_row = conn.execute(
                "SELECT COUNT(*) AS n FROM precheck_history WHERE key_id = ?",
                (key_id,),
            ).fetchone()
            n = int(count_row["n"]) if count_row is not None else 0
            if n > MAX_ROWS_PER_KEY:
                excess = n - MAX_ROWS_PER_KEY
                conn.execute(
                    """
                    DELETE FROM precheck_history
                    WHERE rowid IN (
                      SELECT rowid FROM precheck_history
                      WHERE key_id = ?
                      ORDER BY checked_at ASC
                      LIMIT ?
                    )
                    """,
                    (key_id, excess),
                )
            conn.commit()

    def bulk_insert(
        self,
        rows: list[tuple[str, float, HistoryStatus, float | None, str | None]],
    ) -> None:
        """Insert many rows without heartbeat/prune (test and migration helper)."""
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO precheck_history
                  (key_id, checked_at, status, latency_ms, error)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def record(
        self,
        key_id: str,
        status: HistoryStatus,
        *,
        latency_ms: float | None = None,
        error: str | None = None,
        checked_at: float | None = None,
        force: bool = False,
        do_prune: bool = True,
    ) -> bool:
        """Insert a history row when policy says so; prune unless ``do_prune=False``.

        Returns True if a row was written.
        """
        ts = time.time() if checked_at is None else checked_at
        with self._connect() as conn:
            if force:
                should_write = True
            else:
                latest = self._latest_row(conn, key_id)
                should_write = latest is None
                if not should_write and latest is not None:
                    last_at, last_status = latest
                    if status != last_status or (ts - last_at) >= HEARTBEAT_INTERVAL_S:
                        should_write = True
            if should_write:
                conn.execute(
                    """
                    INSERT INTO precheck_history
                      (key_id, checked_at, status, latency_ms, error)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (key_id, ts, status, latency_ms, error),
                )
                conn.commit()
        if do_prune:
            self.prune(key_id, now=ts)
        return should_write

    def count_for_key(self, key_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM precheck_history WHERE key_id = ?",
                (key_id,),
            ).fetchone()
            return int(row["n"]) if row is not None else 0

    def samples_in_window(
        self, key_id: str, *, window_s: float, now: float | None = None
    ) -> list[HistorySample]:
        ts = time.time() if now is None else now
        since = ts - window_s
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT checked_at, status, latency_ms, error
                FROM precheck_history
                WHERE key_id = ? AND checked_at >= ?
                ORDER BY checked_at ASC
                """,
                (key_id, since),
            ).fetchall()
        out: list[HistorySample] = []
        for row in rows:
            status_raw = str(row["status"])
            status = (
                cast(HistoryStatus, status_raw)
                if status_raw in _VALID_STATUSES
                else cast(HistoryStatus, "error")
            )
            out.append(
                HistorySample(
                    t=float(row["checked_at"]),
                    status=status,
                    latency_ms=row["latency_ms"],
                    error=row["error"],
                )
            )
        return out

    def summarize(
        self,
        key_id: str,
        *,
        window: str = "24h",
        now: float | None = None,
    ) -> HealthSummary:
        window_s = parse_window(window)
        samples = self.samples_in_window(key_id, window_s=window_s, now=now)
        n = len(samples)
        rate_limit_count = sum(1 for s in samples if s.status == "rate_limit")
        if n < 3:
            uptime: float | None = None
        else:
            up = sum(1 for s in samples if s.status in _UP_STATUSES)
            uptime = (up / n) * 100.0

        latencies = sorted(s.latency_ms for s in samples if s.latency_ms is not None)
        current: HistoryStatus | None = samples[-1].status if samples else None

        last_change: float | None = None
        if samples:
            last_change = samples[0].t
            prev = samples[0].status
            for s in samples[1:]:
                if s.status != prev:
                    last_change = s.t
                    prev = s.status

        return HealthSummary(
            key_id=key_id,
            window=window,
            samples=samples,
            uptime_pct=uptime,
            p50_latency_ms=_percentile(latencies, 50.0),
            p95_latency_ms=_percentile(latencies, 95.0),
            current_status=current,
            last_change_at=last_change,
            rate_limit_count=rate_limit_count,
        )

    def to_api_dict(self, summary: HealthSummary) -> dict[str, object]:
        """Shape for ``GET /api/keys/{key_id}/health``."""
        return {
            "key_id": summary.key_id,
            "window": summary.window,
            "samples": [
                {
                    "t": s.t,
                    "status": s.status,
                    "latency_ms": s.latency_ms,
                }
                for s in summary.samples
            ],
            "uptime_pct": summary.uptime_pct,
            "p50_latency_ms": summary.p50_latency_ms,
            "p95_latency_ms": summary.p95_latency_ms,
            "current_status": summary.current_status,
            "last_change_at": summary.last_change_at,
            "rate_limit_count": summary.rate_limit_count,
        }
