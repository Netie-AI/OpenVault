"""Daily token allowance from the catalog and OpenVault's own usage_events.

The limit and the reset zone live on ``ProviderSpec``. This module does not
hard-code a provider id or a token number. ``usage_events`` is read only;
its 18-column schema is not changed.
"""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from openmw.openvault.vault.providers import get_provider

_RESET_REASONS = frozenset(
    {
        "rate_limited",
        "rate_limit_text",
        "credits_exhausted",
        "quota_exhausted",
    }
)


@dataclass(frozen=True)
class QuotaView:
    """Health of one provider against its catalog daily allowance."""

    status: str
    tokens_used: int
    limit: int | None
    reset_at: str | None


def iso_utc(epoch: float) -> str:
    """UTC timestamp with a Z suffix and no fractional seconds."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_bounds(tz_name: str, now: float | None = None) -> tuple[float, float]:
    """``(window_start, next_reset)`` as epoch seconds in ``tz_name``."""
    current = datetime.fromtimestamp(
        time.time() if now is None else now,
        ZoneInfo(tz_name or "UTC"),
    )
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    nxt = start + timedelta(days=1)
    return start.timestamp(), nxt.timestamp()


def seconds_until_reset(tz_name: str, now: float | None = None) -> float:
    _, nxt = window_bounds(tz_name, now)
    current = time.time() if now is None else now
    return max(0.0, nxt - current)


def park_wait_s(provider: str, cooldown_s: float, reason: str) -> float:
    """How long a park lasts.

    A provider with a reset zone and no tracked token ceiling (Google) stays
    parked until that zone's next midnight. Everyone else keeps ``cooldown_s``.
    """
    wait = max(0.0, cooldown_s)
    spec = get_provider(provider)
    if spec is None or spec.daily_token_limit is not None or not spec.quota_reset_tz:
        return wait
    if reason not in _RESET_REASONS:
        return wait
    return seconds_until_reset(spec.quota_reset_tz)


def _tokens_since(db_path: Path, provider: str, since: float) -> int:
    if not db_path.is_file():
        return 0
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
    except sqlite3.Error:
        return 0
    try:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage_events'"
        ).fetchone()
        if found is None:
            return 0
        row = conn.execute(
            "SELECT COALESCE(SUM(total_tokens), 0) FROM usage_events "
            "WHERE provider=? AND created_at>=? AND status>=200 AND status<300",
            (provider, float(since)),
        ).fetchone()
        return int(row[0] if row is not None else 0)
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def quota_view(provider: str, db_path: Path, *, now: float | None = None) -> QuotaView:
    """``ok`` under the catalog ceiling, ``quota_exhausted`` once it is spent."""
    spec = get_provider(provider)
    if spec is None or spec.daily_token_limit is None or spec.daily_token_limit <= 0:
        return QuotaView("ok", 0, None, None)
    tz_name = spec.quota_reset_tz or "UTC"
    start, nxt = window_bounds(tz_name, now)
    used = _tokens_since(db_path, provider, start)
    reset_at = iso_utc(nxt)
    if used >= spec.daily_token_limit:
        return QuotaView("quota_exhausted", used, spec.daily_token_limit, reset_at)
    return QuotaView("ok", used, spec.daily_token_limit, reset_at)


def quota_blocks(provider: str, db_path: Path) -> bool:
    return quota_view(provider, db_path).status != "ok"


def quota_retry_after_s(provider: str) -> int | None:
    """Whole seconds until the catalog window resets. None when untracked."""
    spec = get_provider(provider)
    if spec is None or spec.daily_token_limit is None:
        return None
    tz_name = spec.quota_reset_tz or "UTC"
    return max(1, math.ceil(seconds_until_reset(tz_name)))
