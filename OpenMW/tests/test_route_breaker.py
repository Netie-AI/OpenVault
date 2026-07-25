"""Circuit breaker state-machine tests."""

from __future__ import annotations

from openmw.openvault.route.breaker import (
    STATE_CLOSED,
    STATE_DEGRADED,
    STATE_HALF_OPEN,
    STATE_OPEN,
    CircuitBreaker,
    get_circuit_breaker,
    is_tripping_status,
    reset_all_circuit_breakers,
)


class _Clock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_only_tripping_status_codes() -> None:
    assert is_tripping_status(500)
    assert is_tripping_status(503)
    assert not is_tripping_status(429)
    assert not is_tripping_status(401)


def test_closed_to_degraded_to_open() -> None:
    clock = _Clock()
    breaker = CircuitBreaker("test-provider", get_circuit_breaker("x", "api_key").profile, now=clock.now)
    assert breaker.state == STATE_CLOSED

    breaker.record_failure(status=500)
    breaker.record_failure(status=502)
    breaker.record_failure(status=503)
    assert breaker.state == STATE_DEGRADED

    breaker.record_failure(status=504)
    breaker.record_failure(status=500)
    assert breaker.state == STATE_OPEN
    assert not breaker.can_execute()


def test_open_to_half_open_lazy_recovery() -> None:
    clock = _Clock()
    profile = get_circuit_breaker("y", "local").profile
    breaker = CircuitBreaker("lazy", profile, now=clock.now)

    for _ in range(profile.failure_threshold):
        breaker.record_failure(status=500)
    assert breaker.state == STATE_OPEN

    clock.advance(16)
    assert breaker.can_execute()
    assert breaker.get_status().state == STATE_HALF_OPEN


def test_half_open_success_closes() -> None:
    clock = _Clock()
    profile = get_circuit_breaker("z", "local").profile
    breaker = CircuitBreaker("probe-ok", profile, now=clock.now)
    for _ in range(profile.failure_threshold):
        breaker.record_failure(status=500)
    clock.advance(20)
    breaker.can_execute()
    breaker.acquire_probe_slot()
    breaker.record_success()
    assert breaker.state == STATE_CLOSED
    assert breaker.failure_count == 0


def test_half_open_failure_reopens_with_escalation() -> None:
    clock = _Clock()
    profile = get_circuit_breaker("esc", "local").profile
    breaker = CircuitBreaker("probe-fail", profile, now=clock.now)
    for _ in range(profile.failure_threshold):
        breaker.record_failure(status=500)
    clock.advance(20)
    breaker.can_execute()
    breaker.acquire_probe_slot()
    breaker.record_failure(status=502)
    assert breaker.state == STATE_OPEN
    assert breaker.open_cycle_count == 1
    first_timeout = breaker.get_status().effective_reset_timeout_ms
    breaker.open_cycle_count = 4
    escalated = breaker.get_status().effective_reset_timeout_ms
    assert escalated > first_timeout


def test_non_tripping_status_ignored() -> None:
    breaker = CircuitBreaker("ignore-429", get_circuit_breaker("a", "api_key").profile)
    breaker.record_failure(status=429)
    assert breaker.state == STATE_CLOSED
    assert breaker.failure_count == 0


def test_registry_reset_clears() -> None:
    cb = get_circuit_breaker("reg-test", "api_key")
    cb.record_failure(status=500)
    reset_all_circuit_breakers()
    fresh = get_circuit_breaker("reg-test", "api_key")
    assert fresh.state == STATE_CLOSED
