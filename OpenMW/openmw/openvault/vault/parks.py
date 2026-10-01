"""Persisted hop parks in keys.db.

Additive: a new ``hop_parks`` table. This module does not ALTER ``keys`` or
``usage_events``. ``usage_events`` stays 18 columns.

A row is one key park (``model`` empty) or one (key, model) park. The text
column is a scrubbed provider message, never a request or response body.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

ERROR_TEXT_MAX = 200

# Prefixes before the 32+ catch-all so a short csk- or xai- key still matches.
_SECRET = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._\-]{6,}"
    r"|\bov_[A-Za-z0-9]{6,}"
    r"|\bsk-[A-Za-z0-9_\-]{6,}"
    r"|\bgsk_[A-Za-z0-9_\-]{6,}"
    r"|\bAIza[0-9A-Za-z_\-]{6,}"
    r"|\bnvapi-[A-Za-z0-9_\-]{6,}"
    r"|\bhf_[A-Za-z0-9]{6,}"
    r"|\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+"
    r"|\bcsk-[A-Za-z0-9_\-]{6,}"
    r"|\bxai-[A-Za-z0-9_\-]{6,}"
    r"|[A-Za-z0-9_\-]{32,})"
)


@dataclass(frozen=True)
class ParkRow:
    key_id: str
    model: str
    park_until: float
    reason: str
    error_text: str


def scrub_secrets(text: str) -> str:
    """Replace key-like substrings. The rest of the message stays."""
    return _SECRET.sub("[redacted]", text)


def clip_error_text(text: str, *, limit: int = ERROR_TEXT_MAX) -> str:
    """Scrub first, then cut to ``limit`` so a secret is not sliced in half."""
    cleaned = scrub_secrets(text).replace("\x00", "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit]


def _message_only(raw: str) -> str:
    """Pull a provider message out of an error payload. Not the body."""
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return ""
        return _dig_message(payload)
    if "\n" in text:
        return ""
    return text


def _dig_message(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    err = payload.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    if isinstance(err, str) and err.strip():
        return err.strip()
    msg = payload.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    return ""


def provider_error_text(raw: str | None, *, status: int | None = None) -> str:
    """Short scrubbed provider message. Empty raw becomes ``HTTP {status}``."""
    message = _message_only(raw or "")
    if not message:
        if status is None:
            return ""
        return f"HTTP {status}"
    return clip_error_text(message)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_park_schema(db_path: Path) -> None:
    """Create ``hop_parks`` if it is missing. Does not touch other tables."""
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hop_parks (
              key_id TEXT NOT NULL,
              model TEXT NOT NULL DEFAULT '',
              park_until REAL NOT NULL,
              reason TEXT NOT NULL DEFAULT '',
              error_text TEXT NOT NULL DEFAULT '',
              updated_at REAL NOT NULL,
              PRIMARY KEY (key_id, model)
            )
            """
        )


def save_park(
    db_path: Path,
    *,
    key_id: str,
    model: str,
    park_until: float,
    reason: str,
    error_text: str,
) -> None:
    text = clip_error_text(error_text)
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute(
            """
            INSERT INTO hop_parks (key_id, model, park_until, reason, error_text, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key_id, model) DO UPDATE SET
              park_until=excluded.park_until,
              reason=excluded.reason,
              error_text=excluded.error_text,
              updated_at=excluded.updated_at
            """,
            (key_id, model, float(park_until), reason, text, time.time()),
        )


def delete_park(db_path: Path, key_id: str, model: str = "") -> None:
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute(
            "DELETE FROM hop_parks WHERE key_id=? AND model=?",
            (key_id, model),
        )


def load_parks(db_path: Path) -> list[ParkRow]:
    with contextlib.closing(_connect(db_path)) as conn, conn:
        rows = conn.execute(
            "SELECT key_id, model, park_until, reason, error_text FROM hop_parks"
        ).fetchall()
        return [
            ParkRow(
                key_id=str(row["key_id"]),
                model=str(row["model"] or ""),
                park_until=float(row["park_until"]),
                reason=str(row["reason"] or ""),
                error_text=str(row["error_text"] or ""),
            )
            for row in rows
        ]
