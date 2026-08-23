"""Token-budget dual-bucket rate limiting for the FreeRoute gateway.

Two token buckets guard every gateway call:

* **request bucket** — regulates QPS; one token per call. Blunts empty /
  malformed floods ("denial of wallet") independent of payload size.
* **token bucket** — regulates payload + compute; deducts
  ``prompt_tokens + max_tokens`` per call, *reserved up front* and *refunded*
  once the true generated-token count is known.

Both buckets refill continuously (fractional tokens per second), so there is
no fixed-window boundary to burst against — the smooth refill is the
sliding-window-equivalent the gateway needs.

State lives behind the :class:`BucketStore` protocol. The default
:class:`InMemoryBucketStore` keeps buckets in-process; a Redis + Lua backend
(atomic, cluster-safe, no double-spend across gateways) can be dropped in
later without touching the limiter logic above it.
"""

from __future__ import annotations

import json
import math
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

# One request costs exactly one request-bucket token.
_REQUEST_COST = 1.0
# Fallback output reservation when the caller does not pin ``max_tokens``.
DEFAULT_MAX_OUTPUT_TOKENS = 1024
DEFAULT_TIER = "local"


@dataclass(frozen=True)
class TierLimits:
    """Per-tier budget. Rates are per minute; capacity is the burst ceiling."""

    name: str
    requests_per_min: float
    tokens_per_min: float
    request_burst: float | None = None
    token_burst: float | None = None

    @property
    def request_capacity(self) -> float:
        return self.request_burst if self.request_burst is not None else self.requests_per_min

    @property
    def token_capacity(self) -> float:
        return self.token_burst if self.token_burst is not None else self.tokens_per_min

    @property
    def request_refill_per_sec(self) -> float:
        return self.requests_per_min / 60.0

    @property
    def token_refill_per_sec(self) -> float:
        return self.tokens_per_min / 60.0


# Loopback/dev "local" is effectively unmetered; free/pro are real budgets.
DEFAULT_TIERS: dict[str, TierLimits] = {
    "local": TierLimits("local", requests_per_min=6000, tokens_per_min=6_000_000),
    "free": TierLimits("free", requests_per_min=20, tokens_per_min=40_000),
    "pro": TierLimits("pro", requests_per_min=120, tokens_per_min=400_000),
}


@dataclass
class _Bucket:
    """A single continuously-refilling token bucket."""

    capacity: float
    tokens: float
    refill_per_sec: float
    updated_at: float

    def _refill(self, now: float) -> None:
        if now > self.updated_at:
            gained = (now - self.updated_at) * self.refill_per_sec
            self.tokens = min(self.capacity, self.tokens + gained)
            self.updated_at = now

    def available(self, now: float) -> float:
        self._refill(now)
        return self.tokens

    def try_consume(self, amount: float, now: float) -> bool:
        self._refill(now)
        if self.tokens + 1e-9 >= amount:
            self.tokens = max(0.0, self.tokens - amount)
            return True
        return False

    def refund(self, amount: float, now: float) -> None:
        self._refill(now)
        self.tokens = min(self.capacity, self.tokens + max(0.0, amount))

    def retry_after(self, amount: float, now: float) -> float:
        """Seconds until ``amount`` tokens are available again."""
        self._refill(now)
        deficit = amount - self.tokens
        if deficit <= 0:
            return 0.0
        if self.refill_per_sec <= 0:
            return math.inf
        return deficit / self.refill_per_sec


@dataclass
class ReserveOutcome:
    """Result of an atomic dual-bucket reservation."""

    allowed: bool
    reserved_tokens: int
    retry_after_s: float
    request_remaining: float
    token_remaining: float
    token_capacity: float
    limited_by: str  # "" | "request" | "token"


class BucketStore(Protocol):
    """Where bucket state lives. Swap in Redis+Lua for a cluster."""

    def reserve(
        self,
        identity: str,
        tier: TierLimits,
        request_cost: float,
        token_cost: float,
        now: float,
    ) -> ReserveOutcome: ...

    def refund(self, identity: str, tier: TierLimits, token_amount: float, now: float) -> None: ...

    def snapshot(self, identity: str, tier: TierLimits, now: float) -> tuple[float, float]: ...


class InMemoryBucketStore:
    """Default in-process store. Thread-safe; single-node only."""

    def __init__(self) -> None:
        self._pairs: dict[str, tuple[_Bucket, _Bucket]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(identity: str, tier: TierLimits) -> str:
        return f"{tier.name}:{identity}"

    def _pair_for(self, identity: str, tier: TierLimits, now: float) -> tuple[_Bucket, _Bucket]:
        key = self._key(identity, tier)
        pair = self._pairs.get(key)
        if pair is None:
            request = _Bucket(
                tier.request_capacity, tier.request_capacity, tier.request_refill_per_sec, now
            )
            token = _Bucket(
                tier.token_capacity, tier.token_capacity, tier.token_refill_per_sec, now
            )
            pair = (request, token)
            self._pairs[key] = pair
            return pair
        # Keep capacity/refill synced to a (possibly reconfigured) tier.
        request, token = pair
        request.capacity = tier.request_capacity
        request.refill_per_sec = tier.request_refill_per_sec
        token.capacity = tier.token_capacity
        token.refill_per_sec = tier.token_refill_per_sec
        return pair

    def reserve(
        self,
        identity: str,
        tier: TierLimits,
        request_cost: float,
        token_cost: float,
        now: float,
    ) -> ReserveOutcome:
        with self._lock:
            request, token = self._pair_for(identity, tier, now)
            request_avail = request.available(now)
            token_avail = token.available(now)
            request_ok = request_avail + 1e-9 >= request_cost
            token_ok = token_avail + 1e-9 >= token_cost
            if request_ok and token_ok:
                request.try_consume(request_cost, now)
                token.try_consume(token_cost, now)
                return ReserveOutcome(
                    allowed=True,
                    reserved_tokens=int(token_cost),
                    retry_after_s=0.0,
                    request_remaining=request.tokens,
                    token_remaining=token.tokens,
                    token_capacity=tier.token_capacity,
                    limited_by="",
                )
            request_retry = 0.0 if request_ok else request.retry_after(request_cost, now)
            token_retry = 0.0 if token_ok else token.retry_after(token_cost, now)
            if not token_ok and token_retry >= request_retry:
                limited_by, retry = "token", token_retry
            else:
                limited_by, retry = "request", request_retry
            return ReserveOutcome(
                allowed=False,
                reserved_tokens=0,
                retry_after_s=retry,
                request_remaining=request_avail,
                token_remaining=token_avail,
                token_capacity=tier.token_capacity,
                limited_by=limited_by,
            )

    def refund(self, identity: str, tier: TierLimits, token_amount: float, now: float) -> None:
        with self._lock:
            _request, token = self._pair_for(identity, tier, now)
            token.refund(token_amount, now)

    def snapshot(self, identity: str, tier: TierLimits, now: float) -> tuple[float, float]:
        with self._lock:
            request, token = self._pair_for(identity, tier, now)
            return request.available(now), token.available(now)


def _reset_seconds(remaining: float, capacity: float, refill_per_sec: float) -> int:
    """Whole seconds until the token bucket is full again."""
    if remaining >= capacity or refill_per_sec <= 0:
        return 0
    return math.ceil((capacity - remaining) / refill_per_sec)


def _retry_after_header(retry_after_s: float) -> str:
    seconds = 3600.0 if math.isinf(retry_after_s) else retry_after_s
    return str(max(1, math.ceil(seconds)))


@dataclass
class RateDecision:
    """Outcome the gateway acts on, plus the client-facing headers."""

    allowed: bool
    tier: str
    reserved_tokens: int
    retry_after_s: float
    limit: int
    remaining: int
    reset_s: int
    limited_by: str
    reservation_id: str = ""

    def headers(self) -> dict[str, str]:
        out = {
            "X-RateLimit-Tier": self.tier,
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(self.reset_s),
        }
        if not self.allowed:
            out["Retry-After"] = _retry_after_header(self.retry_after_s)
        return out


def estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    """~4 chars/token heuristic over OpenAI-style ``messages``."""
    chars = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    chars += len(str(part.get("text", "")))
                else:
                    chars += len(str(part))
        elif content is not None:
            chars += len(str(content))
    return max(1, math.ceil(chars / 4))


def usage_total_tokens(result: dict[str, Any] | str) -> int | None:
    """Pull ``usage.total_tokens`` from an upstream response, if present."""
    if not isinstance(result, dict):
        return None
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return None
    total = usage.get("total_tokens")
    if isinstance(total, int | float):
        return int(total)
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if isinstance(prompt, int | float) and isinstance(completion, int | float):
        return int(prompt) + int(completion)
    return None


class SseUsageCapture:
    """Scan SSE bytes for OpenAI stream usage without buffering the whole body.

    Upstream may emit ``usage`` only on a late ``data:`` chunk (after content
    has already been forwarded). Incomplete trailing lines stay in ``_carry``
    until the next ``feed`` or ``finish`` — never invents usage.
    """

    def __init__(self) -> None:
        self._carry = b""
        self.total_tokens: int | None = None

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        data = self._carry + chunk
        lines = data.split(b"\n")
        self._carry = lines[-1]
        for line in lines[:-1]:
            self._consider_line(line)

    def finish(self) -> None:
        if self._carry:
            self._consider_line(self._carry)
            self._carry = b""

    def _consider_line(self, line: bytes) -> None:
        raw = line.strip(b"\r")
        if not raw.startswith(b"data:"):
            return
        payload = raw[5:].strip()
        if not payload or payload == b"[DONE]":
            return
        try:
            obj = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return
        got = usage_total_tokens(obj)
        if got is not None:
            self.total_tokens = got


class TokenBudgetLimiter:
    """Dual-bucket, tier-aware limiter for the FreeRoute gateway."""

    def __init__(
        self,
        tiers: dict[str, TierLimits] | None = None,
        *,
        store: BucketStore | None = None,
        default_tier: str = DEFAULT_TIER,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tiers = dict(tiers) if tiers is not None else dict(DEFAULT_TIERS)
        self._default_tier = (
            default_tier if default_tier in self._tiers else next(iter(self._tiers))
        )
        self._store: BucketStore = store if store is not None else InMemoryBucketStore()
        self._clock = clock
        self._open_reservations: dict[str, int] = {}
        self._open_lock = threading.Lock()

    def tier_for(self, name: str | None) -> TierLimits:
        """Resolve a tier name. An unknown name gets the *smallest* bucket.

        This used to fall back to ``self._default_tier``, which is ``local`` —
        6000 rpm / 6,000,000 tpm. So any tier string nobody had defined resolved
        to the largest bucket in the table: a typo, a renamed tier, or a key
        issued as "unlimited" all failed **open**. An unrecognised tier is a
        configuration mistake, and a mistake must not be more generous than
        every tier we deliberately wrote down.

        ``None`` still means "caller named no tier", which is the loopback dev
        path, and keeps the default.
        """
        if name is None:
            return self._tiers[self._default_tier]
        if name in self._tiers:
            return self._tiers[name]
        return self.smallest_tier()

    def smallest_tier(self) -> TierLimits:
        """The least generous configured tier — what an unknown name resolves to."""
        return min(
            self._tiers.values(),
            key=lambda t: (t.tokens_per_min, t.requests_per_min),
        )

    def known_tiers(self) -> tuple[str, ...]:
        return tuple(self._tiers)

    def reserve(
        self,
        identity: str,
        *,
        tier: str | None = None,
        prompt_tokens: int,
        max_tokens: int,
    ) -> RateDecision:
        limits = self.tier_for(tier)
        cost = max(1, int(prompt_tokens) + max(0, int(max_tokens)))
        now = self._clock()
        outcome = self._store.reserve(identity, limits, _REQUEST_COST, float(cost), now)
        reset_s = _reset_seconds(
            outcome.token_remaining, limits.token_capacity, limits.token_refill_per_sec
        )
        reservation_id = ""
        if outcome.allowed and outcome.reserved_tokens > 0:
            reservation_id = uuid.uuid4().hex
            with self._open_lock:
                self._open_reservations[reservation_id] = outcome.reserved_tokens
        return RateDecision(
            allowed=outcome.allowed,
            tier=limits.name,
            reserved_tokens=outcome.reserved_tokens,
            retry_after_s=outcome.retry_after_s,
            limit=int(limits.token_capacity),
            remaining=int(outcome.token_remaining),
            reset_s=reset_s,
            limited_by=outcome.limited_by,
            reservation_id=reservation_id,
        )

    def settle(
        self,
        identity: str,
        *,
        tier: str | None = None,
        reserved_tokens: int,
        actual_tokens: int,
        reservation_id: str = "",
    ) -> None:
        """Refund reserved-minus-actual once. Same reservation_id is a no-op."""
        if reserved_tokens <= 0:
            return
        if reservation_id:
            with self._open_lock:
                if reservation_id not in self._open_reservations:
                    return
                del self._open_reservations[reservation_id]
        refund = reserved_tokens - max(0, int(actual_tokens))
        if refund <= 0:
            return
        self._store.refund(identity, self.tier_for(tier), float(refund), self._clock())

    def headers_for(self, identity: str, *, tier: str | None = None) -> dict[str, str]:
        limits = self.tier_for(tier)
        _request_remaining, token_remaining = self._store.snapshot(identity, limits, self._clock())
        reset_s = _reset_seconds(
            token_remaining, limits.token_capacity, limits.token_refill_per_sec
        )
        return {
            "X-RateLimit-Tier": limits.name,
            "X-RateLimit-Limit": str(int(limits.token_capacity)),
            "X-RateLimit-Remaining": str(max(0, int(token_remaining))),
            "X-RateLimit-Reset": str(reset_s),
        }

    def status(self, identity: str, *, tier: str | None = None) -> dict[str, Any]:
        limits = self.tier_for(tier)
        request_remaining, token_remaining = self._store.snapshot(identity, limits, self._clock())
        return {
            "identity": identity,
            "tier": limits.name,
            "requests_per_min": limits.requests_per_min,
            "tokens_per_min": limits.tokens_per_min,
            "request_capacity": int(limits.request_capacity),
            "token_capacity": int(limits.token_capacity),
            "request_remaining": int(request_remaining),
            "token_remaining": int(token_remaining),
            # Aliases for Cortex workflow_openvault.check_openfree_budget
            "remaining": int(token_remaining),
            "remaining_tokens": int(token_remaining),
            "reset_s": _reset_seconds(
                token_remaining, limits.token_capacity, limits.token_refill_per_sec
            ),
            "tiers": sorted(self._tiers),
            "backend": type(self._store).__name__,
        }
