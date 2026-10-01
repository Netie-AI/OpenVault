"""OpenRouter key-probe facts. Two columns, nothing else from the body.

``GET https://openrouter.ai/api/v1/key`` can echo the key in ``data.label``
and a large account payload. This table stores ``limit_remaining`` and
``is_free_tier`` only. The provider key and the response body are not columns.
"""

from __future__ import annotations

import contextlib
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS openrouter_probe (
  key_id TEXT PRIMARY KEY,
  limit_remaining REAL,
  is_free_tier INTEGER,
  checked_at REAL NOT NULL
)
"""


@dataclass(frozen=True)
class OpenRouterProbe:
    key_id: str
    limit_remaining: float | None
    is_free_tier: bool | None
    checked_at: float


def openrouter_probe_facts(payload: object) -> tuple[float | None, bool | None]:
    """Copy ``limit_remaining`` and ``is_free_tier``. Drop every other field."""
    data: object = payload
    if isinstance(payload, dict):
        inner = payload.get("data")
        if isinstance(inner, dict):
            data = inner
    if not isinstance(data, dict):
        return None, None
    return _limit_remaining(data.get("limit_remaining")), _free_tier(data.get("is_free_tier"))


def _limit_remaining(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _free_tier(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(_DDL)


def save_openrouter_probe(
    db_path: Path,
    *,
    key_id: str,
    limit_remaining: float | None,
    is_free_tier: bool | None,
) -> None:
    flag = None if is_free_tier is None else int(is_free_tier)
    remaining = None if limit_remaining is None else float(limit_remaining)
    with contextlib.closing(_connect(db_path)) as conn, conn:
        _ensure(conn)
        conn.execute(
            """
            INSERT INTO openrouter_probe
              (key_id, limit_remaining, is_free_tier, checked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key_id) DO UPDATE SET
              limit_remaining=excluded.limit_remaining,
              is_free_tier=excluded.is_free_tier,
              checked_at=excluded.checked_at
            """,
            (key_id, remaining, flag, time.time()),
        )


def delete_openrouter_probe(db_path: Path, key_id: str) -> None:
    with contextlib.closing(_connect(db_path)) as conn, conn:
        _ensure(conn)
        conn.execute("DELETE FROM openrouter_probe WHERE key_id=?", (key_id,))


def load_openrouter_probes(db_path: Path) -> dict[str, OpenRouterProbe]:
    if not db_path.is_file():
        return {}
    with contextlib.closing(_connect(db_path)) as conn, conn:
        _ensure(conn)
        rows = conn.execute(
            """
            SELECT key_id, limit_remaining, is_free_tier, checked_at
            FROM openrouter_probe
            """
        ).fetchall()
    found: dict[str, OpenRouterProbe] = {}
    for row in rows:
        flag = row["is_free_tier"]
        free = None if flag is None else bool(flag)
        remaining = row["limit_remaining"]
        found[str(row["key_id"])] = OpenRouterProbe(
            key_id=str(row["key_id"]),
            limit_remaining=None if remaining is None else float(remaining),
            is_free_tier=free,
            checked_at=float(row["checked_at"]),
        )
    return found
