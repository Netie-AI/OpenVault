"""Per-hop attempt ledger in keys.db.

Additive table ``hop_attempts``. This module does not ALTER ``keys`` or
``usage_events``. A row is one fallback send: no request body, no response
body, and no provider key.

Rows older than ``HOP_ATTEMPT_RETENTION_S`` (7 days) are deleted. The table
is also capped at ``HOP_ATTEMPT_MAX_ROWS`` newest rows.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from openmw.openvault.vault.parks import clip_error_text

HOP_ATTEMPT_RETENTION_S = 7 * 24 * 60 * 60
HOP_ATTEMPT_MAX_ROWS = 2000

_REQUEST_ID_MAX = 64
_KEY_ID_MAX = 80
_MODEL_MAX = 200
_STATUS_MAX = 32


@dataclass(frozen=True)
class HopAttempt:
    request_id: str
    key_id: str
    model: str
    status: str
    latency_ms: int | None
    reason: str
    ts: float


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _plain(text: str, limit: int) -> str:
    cleaned = text.replace("\x00", "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit]


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hop_attempts (
          request_id TEXT NOT NULL,
          key_id TEXT NOT NULL,
          model TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT '',
          latency_ms INTEGER,
          reason TEXT NOT NULL DEFAULT '',
          ts REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_hop_attempts_ts
          ON hop_attempts (ts)
        """
    )


def _prune(conn: sqlite3.Connection, now: float) -> None:
    cutoff = now - HOP_ATTEMPT_RETENTION_S
    conn.execute("DELETE FROM hop_attempts WHERE ts < ?", (cutoff,))
    count_row = conn.execute("SELECT COUNT(*) FROM hop_attempts").fetchone()
    count = int(count_row[0] if count_row is not None else 0)
    extra = count - HOP_ATTEMPT_MAX_ROWS
    if extra <= 0:
        return
    conn.execute(
        """
        DELETE FROM hop_attempts
        WHERE rowid IN (
          SELECT rowid FROM hop_attempts
          ORDER BY ts ASC, rowid ASC
          LIMIT ?
        )
        """,
        (extra,),
    )


def record_hop_attempt(
    db_path: Path,
    *,
    request_id: str,
    key_id: str,
    model: str,
    status: str,
    latency_ms: int,
    reason: str,
    ts: float | None = None,
    now: float | None = None,
) -> None:
    """Insert one attempt and prune. ``reason`` is scrubbed. Bodies are not."""
    written = time.time() if ts is None else float(ts)
    current = time.time() if now is None else float(now)
    latency = max(0, int(latency_ms))
    with contextlib.closing(_connect(db_path)) as conn, conn:
        _ensure(conn)
        conn.execute(
            """
            INSERT INTO hop_attempts
              (request_id, key_id, model, status, latency_ms, reason, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _plain(request_id, _REQUEST_ID_MAX),
                _plain(key_id, _KEY_ID_MAX),
                _plain(model, _MODEL_MAX),
                _plain(status, _STATUS_MAX),
                latency,
                clip_error_text(reason),
                written,
            ),
        )
        _prune(conn, current)


def load_hop_attempts(db_path: Path) -> list[HopAttempt]:
    if not db_path.is_file():
        return []
    with contextlib.closing(_connect(db_path)) as conn, conn:
        _ensure(conn)
        rows = conn.execute(
            """
            SELECT request_id, key_id, model, status, latency_ms, reason, ts
            FROM hop_attempts
            ORDER BY ts ASC, rowid ASC
            """
        ).fetchall()
    return [
        HopAttempt(
            request_id=str(row["request_id"]),
            key_id=str(row["key_id"]),
            model=str(row["model"] or ""),
            status=str(row["status"] or ""),
            latency_ms=None if row["latency_ms"] is None else int(row["latency_ms"]),
            reason=str(row["reason"] or ""),
            ts=float(row["ts"]),
        )
        for row in rows
    ]
