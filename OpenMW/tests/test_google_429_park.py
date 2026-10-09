"""Gemini 429 quota class: per-minute is a short park, per-day stays daily.

No network. Upstream httpx is mocked. Bodies are fixtures, not live calls.
A strict pin still does not call another provider. The pin error names the
park and the wait, and it does not carry a key id or a secret.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from openmw.openvault.app import create_app
from openmw.openvault.route.attempt import QUOTA_PARK_MS, classify_attempt
from openmw.openvault.route.breaker import reset_all_circuit_breakers
from openmw.openvault.vault import fallback as fallback_mod
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.parks import provider_error_text
from openmw.openvault.vault.proxy import (
    PIN_UNAVAILABLE,
    STRICT_PIN_HEADER,
    chat_completions,
)
from openmw.openvault.vault.quota import seconds_until_reset, window_bounds
from openmw.openvault.vault.store import KeyRecord, KeyVault
from openmw.openvault.vault.usage_store import HopTrace

_GEMINI = "gemini-3.5-flash"
_GOOGLE = "https://generativelanguage.googleapis.com/v1beta/openai"
_GROQ = "https://api.groq.com/openai/v1"
_MINUTE_ID = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
_DAY_ID = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
_HOUR_ID = "GenerateRequestsPerHourPerProjectPerModel-FreeTier"
_QUOTA_MESSAGE = (
    "You exceeded your current quota, please check your plan and billing details. "
    "For more information on this error, head to: "
    "https://ai.google.dev/gemini-api/docs/rate-limits."
)
_VAULT_SECRET = "AIza-test-ov162-vaultsecret"
_BODY_DECOY = "AIza-test-ov162-bodydecoy"


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    reset_all_circuit_breakers()
    yield
    reset_all_circuit_breakers()


@pytest.fixture()
def vault(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> KeyVault:
    monkeypatch.delenv("OPENVAULT_LOCAL_BASE_URL", raising=False)
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


def _google_429(quota_id: str, retry_delay: str | None) -> str:
    """Realistic Gemini QuotaFailure body. The decoy must never leave the fixture."""
    details: list[dict[str, Any]] = [
        {
            "@type": "type.googleapis.com/google.rpc.Help",
            "links": [
                {
                    "description": "Learn more about Gemini API quotas",
                    "url": "https://ai.google.dev/gemini-api/docs/rate-limits",
                }
            ],
        },
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [
                {
                    "quotaMetric": (
                        "generativelanguage.googleapis.com/generate_content_free_tier_requests"
                    ),
                    "quotaId": quota_id,
                    "quotaDimensions": {"location": "global", "model": _GEMINI},
                }
            ],
        },
    ]
    if retry_delay is not None:
        details.append(
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}
        )
    return json.dumps(
        {
            "error": {
                "code": 429,
                "message": _QUOTA_MESSAGE,
                "status": "RESOURCE_EXHAUSTED",
                "details": details,
            },
            "decoy": _BODY_DECOY,
        }
    )


def _hop(
    vault: KeyVault,
    *,
    label: str,
    provider: str,
    secret: str,
    base_url: str,
    role: str,
    priority: int,
) -> KeyRecord:
    record = vault.create(
        label=label,
        provider=provider,
        secret=secret,
        role=role,
        priority=priority,
        base_url=base_url,
    )
    vault.set_precheck(record.id, status="ok", latency_ms=10.0, error=None)
    return record


def _response(status: int, body: str, payload: dict[str, Any] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = body
    resp.headers = {}
    if payload is None:
        resp.json = MagicMock(side_effect=ValueError("not json"))
    else:
        resp.json = MagicMock(return_value=payload)
    return resp


def _client(*responses: MagicMock) -> MagicMock:
    mock = MagicMock()
    mock.post = AsyncMock(side_effect=list(responses))
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


def _urls(mock: MagicMock) -> list[str]:
    found: list[str] = []
    for call in mock.post.await_args_list:
        if call.args:
            found.append(str(call.args[0]))
        else:
            found.append(str(call.kwargs.get("url")))
    return found


def test_per_minute_quota_phrase_is_not_credits_exhausted() -> None:
    body = _google_429(_MINUTE_ID, "27s")
    assert "exceeded your current quota" in body
    outcome = classify_attempt(429, body)
    assert outcome.attempt_class == "rate_limit"
    assert outcome.reason == "rate_limited"
    assert outcome.candidate == "park"
    assert outcome.honor_cooldown is True
    assert outcome.cooldown_ms == 27_000


def test_plain_quota_phrase_without_details_stays_credits() -> None:
    outcome = classify_attempt(429, _QUOTA_MESSAGE)
    assert outcome.attempt_class == "quota_exhausted"
    assert outcome.reason == "credits_exhausted"
    assert outcome.cooldown_ms == QUOTA_PARK_MS


def test_per_day_quota_stays_credits_exhausted() -> None:
    body = _google_429(_DAY_ID, "45s")
    assert "exceeded your current quota" in body
    outcome = classify_attempt(429, body)
    assert outcome.attempt_class == "quota_exhausted"
    assert outcome.reason == "credits_exhausted"
    assert outcome.cooldown_ms == QUOTA_PARK_MS


def test_unknown_quota_id_stays_credits_exhausted() -> None:
    body = _google_429(_HOUR_ID, "27s")
    outcome = classify_attempt(429, body)
    assert outcome.attempt_class == "quota_exhausted"
    assert outcome.reason == "credits_exhausted"
    assert outcome.honor_cooldown is False
    assert outcome.cooldown_ms == QUOTA_PARK_MS


@pytest.mark.parametrize("delay", ["1e400s", "-5s", "NaNs", "30"])
def test_rejected_retry_delay_uses_the_short_fallback(delay: str) -> None:
    outcome = classify_attempt(429, _google_429(_MINUTE_ID, delay))
    assert outcome.attempt_class == "rate_limit"
    assert outcome.reason == "rate_limited"
    assert outcome.honor_cooldown is True
    assert outcome.cooldown_ms == 60_000


def test_huge_finite_retry_delay_is_capped() -> None:
    outcome = classify_attempt(429, _google_429(_MINUTE_ID, "99999999s"))
    reset_ms = int(seconds_until_reset("America/Los_Angeles") * 1000.0)
    assert outcome.attempt_class == "rate_limit"
    assert outcome.reason == "rate_limited"
    assert outcome.honor_cooldown is True
    assert 1 <= outcome.cooldown_ms <= 3_600_000
    assert outcome.cooldown_ms <= reset_ms + 2_000
    assert outcome.cooldown_ms < 99_999_999_000


def test_four_hundred_digit_delay_falls_back_to_credits() -> None:
    outcome = classify_attempt(429, _google_429(_MINUTE_ID, "9" * 400 + "s"))
    assert outcome.attempt_class == "quota_exhausted"
    assert outcome.reason == "credits_exhausted"
    assert outcome.honor_cooldown is False
    assert outcome.cooldown_ms == QUOTA_PARK_MS


def test_deeply_nested_429_does_not_raise() -> None:
    nested = '{"error":' * 20_000 + '"x"' + "}" * 20_000
    outcome = classify_attempt(429, nested)
    assert outcome.attempt_class == "rate_limit"
    assert outcome.honor_cooldown is False
    text = provider_error_text(nested, status=429)
    assert text == "HTTP 429"


def test_strict_per_day_parks_until_reset_and_does_not_swap(vault: KeyVault) -> None:
    google = _hop(
        vault,
        label="google",
        provider="google",
        secret=_VAULT_SECRET,
        base_url=_GOOGLE,
        role="primary",
        priority=0,
    )
    _hop(
        vault,
        label="groq",
        provider="groq",
        secret="gsk-test-ov162-groq",
        base_url=_GROQ,
        role="backup",
        priority=10,
    )
    mgr = FallbackManager(vault)
    limited = _response(429, _google_429(_DAY_ID, "45s"))
    other = _response(200, '{"id":"groq-should-not"}', {"id": "groq-should-not", "choices": []})
    mock = _client(limited, other)
    trace = HopTrace()
    before = time.time()
    body = {
        "model": _GEMINI,
        "messages": [{"role": "user", "content": "hi"}],
        "strict": True,
    }
    with (
        capture_logs() as logs,
        patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock),
    ):
        status, payload = asyncio.run(chat_completions(vault, mgr, body, trace=trace))

    assert status == 503
    assert isinstance(payload, dict)
    err = payload["error"]
    assert err["type"] == PIN_UNAVAILABLE
    assert err["message"] == "pinned model has no healthy hop"
    assert err["reason"] == "quota_exhausted"
    assert err["park_reason"] == "credits_exhausted"
    assert err["provider"] == "google"
    assert err["model"] == _GEMINI
    assert payload["served_provider"] is None
    assert payload["served_model"] is None
    assert payload["served_local"] is False
    _, reset = window_bounds("America/Los_Angeles", before)
    until = mgr._circuit(google.id).park_until
    assert until is not None
    assert abs(until - reset) < 2.0
    retry = err["retry_after_s"]
    assert isinstance(retry, int)
    assert abs(retry - max(1, math.ceil(reset - time.time()))) <= 2
    assert trace.retry_after_s == retry
    assert _urls(mock) == [f"{_GOOGLE}/chat/completions"]
    blob = json.dumps({"payload": payload, "logs": logs})
    assert google.id not in blob
    assert _VAULT_SECRET not in blob
    assert _BODY_DECOY not in blob


def test_http_strict_per_minute_pin_names_the_wait(
    vault: KeyVault, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    google = _hop(
        vault,
        label="google",
        provider="google",
        secret=_VAULT_SECRET,
        base_url=_GOOGLE,
        role="primary",
        priority=0,
    )
    _hop(
        vault,
        label="groq",
        provider="groq",
        secret="gsk-test-ov162-other",
        base_url=_GROQ,
        role="backup",
        priority=10,
    )
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    client = TestClient(app, client=("127.0.0.1", 5555))
    identity, headers = issue_key(client, tier="free")
    limited = _response(429, _google_429(_MINUTE_ID, "27s"))
    other = _response(200, '{"id":"nope"}', {"id": "nope", "choices": []})
    mock = _client(limited, other)
    chat = {"model": _GEMINI, "messages": [{"role": "user", "content": "hi"}]}
    with (
        capture_logs() as logs,
        patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock),
    ):
        first = client.post(
            "/v1/chat/completions",
            json=chat,
            headers={**headers, STRICT_PIN_HEADER: "true"},
        )
        second = client.post(
            "/v1/chat/completions",
            json={**chat, "strict": True},
            headers=headers,
        )

    assert first.status_code == 503
    body = first.json()
    err = body["error"]
    assert err == {
        "message": "pinned model has no healthy hop",
        "type": PIN_UNAVAILABLE,
        "model": _GEMINI,
        "reason": "parked",
        "provider": "google",
        "park_reason": "rate_limited",
        "retry_after_s": err["retry_after_s"],
    }
    assert body["served_provider"] is None
    assert body["served_model"] is None
    assert body["served_local"] is False
    assert 1 <= err["retry_after_s"] <= 27
    assert first.headers.get("Retry-After") == str(err["retry_after_s"])
    assert second.status_code == 503
    again = second.json()["error"]
    assert again["park_reason"] == "rate_limited"
    assert again["provider"] == "google"
    assert again["model"] == _GEMINI
    assert second.headers.get("Retry-After") == str(again["retry_after_s"])
    assert 1 <= again["retry_after_s"] <= 27
    assert _urls(mock) == [f"{_GOOGLE}/chat/completions"]
    leaked = first.text + second.text + json.dumps(logs)
    assert google.id not in leaked
    assert identity not in leaked
    assert _VAULT_SECRET not in leaked
    assert _BODY_DECOY not in leaked
    assert FallbackManager(vault).key_park_reason(google.id) == "rate_limited"


def test_non_strict_short_park_is_skipped_then_expires(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    google = _hop(
        vault,
        label="google",
        provider="google",
        secret=_VAULT_SECRET,
        base_url=_GOOGLE,
        role="primary",
        priority=0,
    )
    _hop(
        vault,
        label="groq",
        provider="groq",
        secret="gsk-test-ov162-walk",
        base_url=_GROQ,
        role="backup",
        priority=10,
    )
    mgr = FallbackManager(vault)
    limited = _response(429, _google_429(_MINUTE_ID, "27s"))
    ok = _response(200, '{"id":"ok","choices":[]}', {"id": "ok", "choices": []})
    mock = _client(limited, ok, ok, ok)
    chat = {"model": _GEMINI, "messages": [{"role": "user", "content": "hi"}]}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        first_status, first_payload = asyncio.run(chat_completions(vault, mgr, chat))
        assert first_status == 200
        assert isinstance(first_payload, dict)
        assert first_payload["served_provider"] == "groq"
        until = mgr._circuit(google.id).park_until
        assert until is not None
        assert mgr.key_park_reason(google.id) == "rate_limited"
        assert abs((until - time.time()) - 27.0) < 2.0
        _, reset = window_bounds("America/Los_Angeles", time.time())
        assert abs(until - reset) > 60

        second_status, second_payload = asyncio.run(chat_completions(vault, mgr, chat))
        assert second_status == 200
        assert isinstance(second_payload, dict)
        assert second_payload["served_provider"] == "groq"
        assert _urls(mock) == [
            f"{_GOOGLE}/chat/completions",
            f"{_GROQ}/chat/completions",
            f"{_GROQ}/chat/completions",
        ]

        monkeypatch.setattr(fallback_mod.time, "time", lambda: until + 1.0)
        assert mgr.key_is_parked(google.id) is False
        third_status, third_payload = asyncio.run(chat_completions(vault, mgr, chat))

    assert third_status == 200
    assert isinstance(third_payload, dict)
    assert third_payload["served_provider"] == "google"
    assert third_payload["served_model"] == _GEMINI
    assert _urls(mock)[-1] == f"{_GOOGLE}/chat/completions"


def _two_hops(vault: KeyVault) -> tuple[KeyRecord, FallbackManager]:
    google = _hop(
        vault,
        label="google",
        provider="google",
        secret=_VAULT_SECRET,
        base_url=_GOOGLE,
        role="primary",
        priority=0,
    )
    _hop(
        vault,
        label="groq",
        provider="groq",
        secret="gsk-test-ov162-walk",
        base_url=_GROQ,
        role="backup",
        priority=10,
    )
    return google, FallbackManager(vault)


def test_plain_text_google_429_parks_until_midnight(vault: KeyVault) -> None:
    google, mgr = _two_hops(vault)
    text = "Resource has been exhausted (e.g. check quota)."
    mock = _client(
        _response(429, text),
        _response(200, '{"id":"ok","choices":[]}', {"id": "ok", "choices": []}),
    )
    chat = {"model": _GEMINI, "messages": [{"role": "user", "content": "hi"}]}
    before = time.time()
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        status, payload = asyncio.run(chat_completions(vault, mgr, chat))
    assert status == 200
    assert isinstance(payload, dict)
    assert payload["served_provider"] == "groq"
    until = mgr._circuit(google.id).park_until
    _, reset = window_bounds("America/Los_Angeles", before)
    assert until is not None
    assert abs(until - reset) < 2.0
    assert mgr.key_park_reason(google.id) == "rate_limited"


def test_huge_retry_delay_park_stays_inside_the_cap(vault: KeyVault) -> None:
    google, mgr = _two_hops(vault)
    mock = _client(
        _response(429, _google_429(_MINUTE_ID, "99999999s")),
        _response(200, '{"id":"ok","choices":[]}', {"id": "ok", "choices": []}),
    )
    chat = {"model": _GEMINI, "messages": [{"role": "user", "content": "hi"}]}
    before = time.time()
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        status, _payload = asyncio.run(chat_completions(vault, mgr, chat))
    assert status == 200
    until = mgr._circuit(google.id).park_until
    _, reset = window_bounds("America/Los_Angeles", before)
    assert until is not None
    wait = until - before
    assert 0 < wait <= 3600.0 + 2.0
    assert until <= reset + 2.0
    assert wait < 99_999_999
    assert mgr.key_park_reason(google.id) == "rate_limited"


def test_four_hundred_digit_delay_does_not_crash_the_handler(vault: KeyVault) -> None:
    google, mgr = _two_hops(vault)
    mock = _client(
        _response(429, _google_429(_MINUTE_ID, "9" * 400 + "s")),
        _response(200, '{"id":"ok","choices":[]}', {"id": "ok", "choices": []}),
    )
    chat = {"model": _GEMINI, "messages": [{"role": "user", "content": "hi"}]}
    before = time.time()
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        status, payload = asyncio.run(chat_completions(vault, mgr, chat))
    assert status == 200
    assert isinstance(payload, dict)
    until = mgr._circuit(google.id).park_until
    _, reset = window_bounds("America/Los_Angeles", before)
    assert until is not None
    assert abs(until - reset) < 2.0
    assert mgr.key_park_reason(google.id) == "credits_exhausted"


def test_deeply_nested_429_does_not_crash_the_handler(vault: KeyVault) -> None:
    nested = '{"error":' * 20_000 + '"x"' + "}" * 20_000
    google, mgr = _two_hops(vault)
    mock = _client(
        _response(429, nested),
        _response(200, '{"id":"ok","choices":[]}', {"id": "ok", "choices": []}),
    )
    chat = {"model": _GEMINI, "messages": [{"role": "user", "content": "hi"}]}
    before = time.time()
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        status, _payload = asyncio.run(chat_completions(vault, mgr, chat))
    assert status == 200
    until = mgr._circuit(google.id).park_until
    _, reset = window_bounds("America/Los_Angeles", before)
    assert until is not None
    assert abs(until - reset) < 2.0
    assert mgr.key_park_reason(google.id) == "rate_limited"
