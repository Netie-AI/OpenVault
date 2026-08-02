"""Sliding-window rate limiter — true trailing window, no internal queue."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

MAX_KEYS = 5000


@dataclass(frozen=True)
class RateLimitWindow:
    requests: int
    window_ms: int


@dataclass(frozen=True)
class AcquireResult:
    allowed: bool
    retry_after_ms: int


class SlidingWindowLimiter:
    """Enforce at most N requests in any trailing windowMs for a key."""

    def __init__(self, *, now: Callable[[], float] | None = None) -> None:
        self._hits: dict[str, list[float]] = {}
        self._now = now or (lambda: __import__("time").time() * 1000)

    def try_acquire(self, key: str, window: RateLimitWindow) -> AcquireResult:
        requests = window.requests
        window_ms = window.window_ms
        if not (requests > 0) or not (window_ms > 0):
            return AcquireResult(allowed=True, retry_after_ms=0)

        now = self._now()
        cutoff = now - window_ms
        previous = self._hits.get(key)
        live = [ts for ts in previous] if previous else []
        live = [ts for ts in live if ts > cutoff]

        if len(live) >= requests:
            retry_after_ms = max(0, int(live[0] + window_ms - now))
            self._hits[key] = live
            return AcquireResult(allowed=False, retry_after_ms=retry_after_ms)

        live.append(now)
        self._set(key, live)
        return AcquireResult(allowed=True, retry_after_ms=0)

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)

    def _set(self, key: str, live: list[float]) -> None:
        if key not in self._hits and len(self._hits) >= MAX_KEYS:
            oldest = next(iter(self._hits))
            del self._hits[oldest]
        self._hits[key] = live
