"""Attempt policy: rate limits park keys; hard fails open circuits.

Pins DESIGN_TIERED_QUEUE_LB §4.2 / Slice 1 — especially the live bug where
three 429s opened a healthy hop circuit for 60s.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from openmw.openvault.route.attempt import (
    DEFAULT_RATE_LIMIT_PARK_MS,
    classify_attempt,
)
from openmw.openvault.route.breaker import (
    STATE_CLOSED,
    STATE_DEGRADED,
    get_circuit_breaker,
    reset_all_circuit_breakers,
)
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackConfig, FallbackManager
from openmw.openvault.vault.proxy import chat_completions
from openmw.openvault.vault.store import KeyVault


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    reset_all_circuit_breakers()
    yield
    reset_all_circuit_breakers()


@pytest.fixture()
def vault(tmp_path: Any) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


def test_three_429s_park_without_opening_circuit(vault: KeyVault) -> None:
    primary = vault.create(
        label="primary",
        provider="openai",
        secret="sk-test-primary-aaaa",
        role="primary",
        priority=1,
    )
    backup = vault.create(
        label="backup",
        provider="openai",
        secret="sk-test-backup-bbbb",
        role="backup",
        priority=1,
    )
    mgr = FallbackManager(vault)

    for _ in range(3):
        outcome = classify_attempt(429, "too many requests")
        assert outcome.attempt_class == "rate_limit"
        assert outcome.counts_as_hard_fail is False
        mgr.record_park(primary.id, outcome.cooldown_ms, outcome.reason or "rate_limited")

    circ = mgr._circuit(primary.id)
    assert circ.state == "closed"
    assert circ.failures == 0
    assert circ.park_until is not None
    assert circ.park_until > time.time()
    ordered = mgr.ordered_candidates()
    assert [r.id for r in ordered] == [backup.id]


def test_retry_after_header_sets_park_duration() -> None:
    outcome = classify_attempt(
        429,
        "rate limited",
        headers={"Retry-After": "30"},
    )
    assert outcome.attempt_class == "rate_limit"
    assert outcome.cooldown_ms >= 29_000
    assert outcome.cooldown_ms <= 31_000


def test_default_rate_limit_park_is_five_seconds() -> None:
    outcome = classify_attempt(429, "slow down")
    assert outcome.cooldown_ms == DEFAULT_RATE_LIMIT_PARK_MS


def test_three_503s_open_hop_circuit(vault: KeyVault) -> None:
    key = vault.create(
        label="flaky",
        provider="openai",
        secret="sk-test-flaky-cccc",
        role="primary",
        priority=1,
    )
    mgr = FallbackManager(vault, config=FallbackConfig(failure_threshold=3, open_seconds=60.0))
    breaker = get_circuit_breaker("openai")

    for _ in range(3):
        outcome = classify_attempt(503, "unavailable")
        assert outcome.counts_as_hard_fail is True
        assert outcome.trip_provider_breaker is True
        mgr.record_failure(key.id, "HTTP 503")
        breaker.record_failure(status=503)

    assert mgr._circuit(key.id).state == "open"
    assert breaker.get_status().failure_count == 3
    assert breaker.get_status().state in (STATE_CLOSED, STATE_DEGRADED)


def test_context_overflow_is_non_mutating() -> None:
    outcome = classify_attempt(400, "input is too long for the model")
    assert outcome.attempt_class == "context_overflow"
    assert outcome.candidate == "eject_for_job"
    assert outcome.counts_as_hard_fail is False
    assert outcome.trip_provider_breaker is False


def test_generic_401_quarantines_but_job_continues() -> None:
    outcome = classify_attempt(401, "invalid api key")
    assert outcome.attempt_class == "auth_fail"
    assert outcome.candidate == "quarantine_key"
    assert outcome.job == "continue_chain"


def test_non_retryable_kills_job_not_key() -> None:
    outcome = classify_attempt(422, "malformed json schema")
    assert outcome.attempt_class == "non_retryable"
    assert outcome.job == "dead"
    assert outcome.counts_as_hard_fail is False


def test_proxy_429_then_backup_succeeds(vault: KeyVault) -> None:
    import asyncio

    primary = vault.create(
        label="primary",
        provider="openai",
        secret="sk-test-primary-dddd",
        role="primary",
        priority=1,
        base_url="https://api.openai.com/v1",
    )
    backup = vault.create(
        label="backup",
        provider="openai",
        secret="sk-test-backup-eeee",
        role="backup",
        priority=1,
        base_url="https://api.openai.com/v1",
    )
    vault.set_precheck(primary.id, status="ok", latency_ms=10.0, error=None)
    vault.set_precheck(backup.id, status="ok", latency_ms=11.0, error=None)
    mgr = FallbackManager(vault)

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.text = "rate limit"
    resp_429.headers = {"Retry-After": "5"}
    resp_429.json = MagicMock(side_effect=ValueError("not json"))

    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.text = '{"id":"chatcmpl-ok"}'
    resp_ok.headers = {}
    resp_ok.json = MagicMock(return_value={"id": "chatcmpl-ok", "choices": []})

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=[resp_429, resp_ok])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock_client):
        status, payload = asyncio.run(
            chat_completions(
                vault,
                mgr,
                {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
            )
        )

    assert status == 200
    assert isinstance(payload, dict)
    assert payload.get("id") == "chatcmpl-ok"

    circ = mgr._circuit(primary.id)
    assert circ.state == "closed"
    assert circ.failures == 0
    assert circ.park_until is not None
    assert mgr._circuit(backup.id).state == "closed"
