"""Provider circuit breaker — CLOSED → DEGRADED → OPEN → HALF_OPEN.

Ports OmniRoute ``circuitBreaker.ts``. Only HTTP 408/500/502/503/504 trip the
breaker; connection-scoped 429 rate limits stay outside this layer.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

CircuitState = Literal["CLOSED", "DEGRADED", "OPEN", "HALF_OPEN"]

STATE_CLOSED: CircuitState = "CLOSED"
STATE_DEGRADED: CircuitState = "DEGRADED"
STATE_OPEN: CircuitState = "OPEN"
STATE_HALF_OPEN: CircuitState = "HALF_OPEN"

TRIP_STATUS_CODES: frozenset[int] = frozenset({408, 500, 502, 503, 504})

BreakerProfileName = Literal["oauth", "api_key", "local"]

MAX_REGISTRY_SIZE = 500
_COLD_IDLE_MS = 30 * 60 * 1000
_SWEEP_INTERVAL_S = 5 * 60


def is_tripping_status(status: int) -> bool:
    """Return True when an HTTP status should increment the provider breaker."""
    return status in TRIP_STATUS_CODES


@dataclass(frozen=True)
class BreakerProfile:
    """Per-credential-class thresholds (plan §2D defaults)."""

    name: BreakerProfileName
    failure_threshold: int
    reset_timeout_ms: int
    half_open_requests: int = 1
    degradation_threshold: int | None = None

    @property
    def degradation_at(self) -> int:
        if self.degradation_threshold is not None:
            return self.degradation_threshold
        return max(1, math.ceil(self.failure_threshold * 0.6))


DEFAULT_PROFILES: dict[BreakerProfileName, BreakerProfile] = {
    "oauth": BreakerProfile("oauth", failure_threshold=3, reset_timeout_ms=60_000),
    "api_key": BreakerProfile("api_key", failure_threshold=5, reset_timeout_ms=30_000),
    "local": BreakerProfile("local", failure_threshold=2, reset_timeout_ms=15_000),
}


@dataclass
class TransitionRecord:
    from_state: CircuitState
    to_state: CircuitState
    timestamp: float
    failure_count: int
    reason: str | None = None


@dataclass
class CircuitBreakerStatus:
    name: str
    state: CircuitState
    failure_count: int
    last_failure_time: float | None
    retry_after_ms: int
    open_cycle_count: int
    degradation_threshold: int
    effective_reset_timeout_ms: int
    profile: BreakerProfileName

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "state": self.state,
            "failureCount": self.failure_count,
            "lastFailureTime": self.last_failure_time,
            "retryAfterMs": self.retry_after_ms,
            "openCycleCount": self.open_cycle_count,
            "degradationThreshold": self.degradation_threshold,
            "effectiveResetTimeoutMs": self.effective_reset_timeout_ms,
            "profile": self.profile,
        }


class CircuitBreakerOpenError(Exception):
    """Raised when a call is short-circuited because the breaker is OPEN."""

    def __init__(self, message: str, circuit_name: str, retry_after_ms: int) -> None:
        super().__init__(message)
        self.circuit_name = circuit_name
        self.retry_after_ms = retry_after_ms


class CircuitBreaker:
    """Single named breaker with lazy OPEN → HALF_OPEN recovery."""

    max_backoff_multiplier: int = 16
    backoff_escalation_count: int = 3
    max_transition_history: int = 20

    def __init__(
        self,
        name: str,
        profile: BreakerProfile,
        *,
        on_state_change: Callable[[str, CircuitState, CircuitState], None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.name = name
        self.profile = profile
        self.on_state_change = on_state_change
        self._now = now or time.time

        self.state: CircuitState = STATE_CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: float | None = None
        self.half_open_allowed = 0
        self.open_cycle_count = 0
        self.transition_history: list[TransitionRecord] = []

    def _effective_reset_timeout_ms(self) -> int:
        base = self.profile.reset_timeout_ms
        if self.open_cycle_count <= self.backoff_escalation_count:
            return base
        factor = 2 ** (self.open_cycle_count - self.backoff_escalation_count)
        return min(base * factor, base * self.max_backoff_multiplier)

    def _time_until_reset_ms(self) -> int:
        if self.last_failure_time is None:
            return 0
        elapsed_ms = int((self._now() - self.last_failure_time) * 1000)
        return max(0, self._effective_reset_timeout_ms() - elapsed_ms)

    def _refresh_open_state(self) -> None:
        if self.state == STATE_OPEN and self._time_until_reset_ms() <= 0:
            self._transition(STATE_HALF_OPEN, "timeout-elapsed")
            self.half_open_allowed = self.profile.half_open_requests

    def _transition(self, new_state: CircuitState, reason: str | None = None) -> None:
        old = self.state
        self.state = new_state
        if new_state == STATE_HALF_OPEN:
            self.half_open_allowed = self.profile.half_open_requests
        self.transition_history.append(
            TransitionRecord(
                from_state=old,
                to_state=new_state,
                timestamp=self._now(),
                failure_count=self.failure_count,
                reason=reason,
            )
        )
        if len(self.transition_history) > self.max_transition_history:
            self.transition_history.pop(0)
        if self.on_state_change and old != new_state:
            self.on_state_change(self.name, old, new_state)

    def can_execute(self) -> bool:
        self._refresh_open_state()
        if self.state in (STATE_CLOSED, STATE_DEGRADED):
            return True
        if self.state == STATE_OPEN:
            return False
        return self.half_open_allowed > 0

    def get_status(self) -> CircuitBreakerStatus:
        self._refresh_open_state()
        retry = 0
        if self.state in (STATE_OPEN, STATE_HALF_OPEN):
            retry = self._time_until_reset_ms()
        return CircuitBreakerStatus(
            name=self.name,
            state=self.state,
            failure_count=self.failure_count,
            last_failure_time=self.last_failure_time,
            retry_after_ms=retry,
            open_cycle_count=self.open_cycle_count,
            degradation_threshold=self.profile.degradation_at,
            effective_reset_timeout_ms=self._effective_reset_timeout_ms(),
            profile=self.profile.name,
        )

    def record_success(self) -> None:
        self._refresh_open_state()
        if self.state == STATE_HALF_OPEN:
            self._transition(STATE_CLOSED, "probe-success")
            self.failure_count = 0
            self.open_cycle_count = 0
        elif self.state == STATE_DEGRADED:
            self.failure_count = max(0, self.failure_count - 1)
            if self.failure_count <= self.profile.degradation_at:
                self._transition(STATE_CLOSED, "recovery")
        else:
            self.failure_count = 0
            self.open_cycle_count = 0
        self.last_failure_time = None

    def record_failure(self, *, status: int | None = None) -> None:
        if status is not None and not is_tripping_status(status):
            return
        self.failure_count += 1
        self.last_failure_time = self._now()

        if self.state == STATE_HALF_OPEN:
            self.open_cycle_count += 1
            self._transition(STATE_OPEN, f"probe-failed (cycle {self.open_cycle_count})")
            return

        if self.failure_count >= self.profile.failure_threshold:
            self._open("threshold-reached")
        elif self.failure_count >= self.profile.degradation_at and self.state == STATE_CLOSED:
            self._transition(
                STATE_DEGRADED,
                f"elevated-failures ({self.failure_count}/{self.profile.failure_threshold})",
            )

    def _open(self, reason: str) -> None:
        self._transition(STATE_OPEN, reason)

    def reset(self) -> None:
        self._transition(STATE_CLOSED, "manual-reset")
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.open_cycle_count = 0
        self.half_open_allowed = 0

    def acquire_probe_slot(self) -> bool:
        """Consume a HALF_OPEN probe slot; return False when none remain."""
        self._refresh_open_state()
        if self.state == STATE_HALF_OPEN and self.half_open_allowed > 0:
            self.half_open_allowed -= 1
            return True
        return self.state in (STATE_CLOSED, STATE_DEGRADED)


_registry: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()
_sweep_started = False


def _evict_cold_breakers() -> None:
    if len(_registry) < MAX_REGISTRY_SIZE:
        return
    candidates: list[tuple[float, str]] = []
    for name, breaker in _registry.items():
        status = breaker.get_status()
        if status.state == STATE_CLOSED and status.failure_count == 0:
            last = (status.last_failure_time or 0) * 1000
            candidates.append((last, name))
    candidates.sort()
    target = len(_registry) - MAX_REGISTRY_SIZE + 1
    for _, name in candidates[:target]:
        _registry.pop(name, None)


def _sweep_registry() -> None:
    now_ms = time.time() * 1000
    to_delete: list[str] = []
    for name, breaker in _registry.items():
        status = breaker.get_status()
        if (
            status.state == STATE_CLOSED
            and status.failure_count == 0
            and (
                status.last_failure_time is None
                or now_ms - status.last_failure_time * 1000 > _COLD_IDLE_MS
            )
        ):
            to_delete.append(name)
    for name in to_delete:
        _registry.pop(name, None)


def _ensure_sweep_timer() -> None:
    global _sweep_started
    if _sweep_started:
        return
    _sweep_started = True
    timer = threading.Timer(_SWEEP_INTERVAL_S, _periodic_sweep)
    timer.daemon = True
    timer.start()


def _periodic_sweep() -> None:
    with _registry_lock:
        _sweep_registry()
    timer = threading.Timer(_SWEEP_INTERVAL_S, _periodic_sweep)
    timer.daemon = True
    timer.start()


def get_circuit_breaker(
    name: str,
    profile: BreakerProfileName = "api_key",
) -> CircuitBreaker:
    """Return a registry-backed breaker, creating one on first access."""
    _ensure_sweep_timer()
    with _registry_lock:
        if name not in _registry:
            _evict_cold_breakers()
            _registry[name] = CircuitBreaker(name, DEFAULT_PROFILES[profile])
        return _registry[name]


def get_all_breaker_statuses() -> list[CircuitBreakerStatus]:
    with _registry_lock:
        return [cb.get_status() for cb in _registry.values()]


def reset_circuit_breaker(name: str) -> CircuitBreakerStatus | None:
    with _registry_lock:
        breaker = _registry.get(name)
        if breaker is None:
            return None
        breaker.reset()
        return breaker.get_status()


def reset_all_circuit_breakers() -> None:
    with _registry_lock:
        for breaker in _registry.values():
            breaker.reset()
        _registry.clear()


def registry_size_for_tests() -> int:
    with _registry_lock:
        return len(_registry)
