"""Strict model pin (#80): opt-in, exact catalog id, fast pin_unavailable.

No network. Upstream httpx is mocked. A strict request never calls a provider
that does not serve the pinned catalog id, and a park sets Retry-After.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.route.attempt import QUOTA_PARK_MS
from openmw.openvault.route.breaker import get_circuit_breaker, reset_all_circuit_breakers
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.local_hop import SERVED_PROVIDER_HEADER
from openmw.openvault.vault.providers import models_for
from openmw.openvault.vault.proxy import (
    PIN_UNAVAILABLE,
    STRICT_PIN_HEADER,
    chat_completions,
    prepare_chat_stream,
)
from openmw.openvault.vault.store import KeyRecord, KeyVault
from openmw.openvault.vault.usage_store import HopTrace

_PIN = "openai/gpt-oss-120b"
_GROQ = "https://api.groq.com/openai/v1"
_TOGETHER = "https://api.together.xyz/v1"
_GOOGLE = "https://generativelanguage.googleapis.com/v1beta/openai"
_GOOGLE_MODEL = models_for("google")[0]
_REQ = "REQBODY-ov80"


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


def _hop(
    vault: KeyVault,
    *,
    label: str,
    provider: str,
    secret: str,
    base_url: str,
    priority: int,
) -> KeyRecord:
    record = vault.create(
        label=label,
        provider=provider,
        secret=secret,
        role="primary",
        priority=priority,
        base_url=base_url,
    )
    vault.set_precheck(record.id, status="ok", latency_ms=10.0, error=None)
    return record


def _groq(vault: KeyVault, priority: int = 0, secret: str = "gsk-test-ov80-aaaa") -> KeyRecord:
    return _hop(
        vault,
        label=f"groq-{priority}",
        provider="groq",
        secret=secret,
        base_url=_GROQ,
        priority=priority,
    )


def _together(vault: KeyVault, priority: int = 0, secret: str = "tg-test-ov98-eeee") -> KeyRecord:
    return _hop(
        vault,
        label=f"together-{priority}",
        provider="together",
        secret=secret,
        base_url=_TOGETHER,
        priority=priority,
    )


def _google(vault: KeyVault) -> KeyRecord:
    return _hop(
        vault,
        label="google",
        provider="google",
        secret="AIza-test-ov80-bbbb",
        base_url=_GOOGLE,
        priority=5,
    )


def _response(status: int, payload: dict[str, Any] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = ""
    resp.headers = {}
    resp.json = MagicMock(return_value=payload if payload is not None else {"id": "x"})
    return resp


class _Recorder:
    def __init__(self, *responses: MagicMock) -> None:
        self.urls: list[str] = []
        self.bodies: list[dict[str, Any]] = []
        self._responses = list(responses)
        mock = MagicMock()
        mock.post = AsyncMock(side_effect=self._post)
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        self.mock = mock

    async def _post(self, url: str, **kwargs: Any) -> MagicMock:
        self.urls.append(url)
        body = kwargs.get("json") or {}
        self.bodies.append(body)
        if not self._responses:
            raise AssertionError(f"unexpected upstream call {url}")
        return self._responses.pop(0)

    @property
    def providers(self) -> list[str]:
        found: list[str] = []
        for url in self.urls:
            if "groq.com" in url:
                found.append("groq")
            elif "together.xyz" in url:
                found.append("together")
            elif "googleapis.com" in url:
                found.append("google")
            else:
                found.append(url)
        return found


def _chat(model: str, *, strict: bool | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": _REQ}]}
    if strict is not None:
        body["strict"] = strict
    return body


def _run(
    vault: KeyVault,
    mgr: FallbackManager,
    body: dict[str, Any],
    recorder: _Recorder,
    trace: HopTrace | None = None,
) -> tuple[int, Any]:
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=recorder.mock):
        return asyncio.run(chat_completions(vault, mgr, body, trace=trace))


def test_strict_healthy_pin_serves_that_model_and_provider(vault: KeyVault) -> None:
    _groq(vault)
    _google(vault)
    mgr = FallbackManager(vault)
    upstream = {
        "id": "chatcmpl-pin",
        "model": "gemini-3.5-flash",
        "choices": [{"message": {"content": "ok"}}],
    }
    rec = _Recorder(_response(200, upstream))
    trace = HopTrace()

    started = time.monotonic()
    status, payload = _run(vault, mgr, _chat(_PIN, strict=True), rec, trace)
    elapsed = time.monotonic() - started

    assert status == 200
    assert elapsed < 1.0
    assert isinstance(payload, dict)
    assert payload["served_provider"] == "groq"
    assert payload["served_model"] == _PIN
    assert payload["served_model"] != "gemini-3.5-flash"
    assert payload["served_local"] is False
    assert trace.provider == "groq"
    assert trace.model_served == _PIN
    assert rec.providers == ["groq"]
    assert rec.bodies[0]["model"] == _PIN
    assert "strict" not in rec.bodies[0]


def test_strict_parked_pin_is_pin_unavailable_without_another_provider(vault: KeyVault) -> None:
    groq = _groq(vault)
    _google(vault)
    mgr = FallbackManager(vault)
    mgr.record_park(groq.id, 30_000, "rate_limited")
    rec = _Recorder(_response(200, {"id": "should-not-run"}))
    trace = HopTrace()

    started = time.monotonic()
    status, payload = _run(vault, mgr, _chat(_PIN, strict=True), rec, trace)
    elapsed = time.monotonic() - started

    assert status == 503
    assert elapsed < 1.0
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == PIN_UNAVAILABLE
    assert payload["error"]["reason"] == "parked"
    assert payload["error"]["model"] == _PIN
    assert payload["served_provider"] is None
    assert payload["served_model"] is None
    assert trace.error_type == PIN_UNAVAILABLE
    assert trace.retry_after_s is not None
    assert 1 <= trace.retry_after_s <= 30
    assert rec.urls == []
    assert rec.providers == []


def test_strict_quota_exhausted_pin_sets_retry_after_and_skips_other_providers(
    vault: KeyVault,
) -> None:
    groq = _groq(vault)
    _google(vault)
    mgr = FallbackManager(vault)
    mgr.record_park(groq.id, QUOTA_PARK_MS, "credits_exhausted")
    rec = _Recorder(_response(200, {"id": "should-not-run"}))
    trace = HopTrace()

    status, payload = _run(vault, mgr, _chat(_PIN, strict=True), rec, trace)

    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == PIN_UNAVAILABLE
    assert payload["error"]["reason"] == "quota_exhausted"
    assert payload["served_provider"] is None
    assert payload["served_model"] is None
    assert trace.retry_after_s is not None
    assert trace.retry_after_s >= 60
    assert rec.providers == []


def test_strict_model_park_does_not_call_another_provider(vault: KeyVault) -> None:
    groq = _groq(vault)
    _google(vault)
    mgr = FallbackManager(vault)
    mgr.record_model_park(groq.id, _PIN, 15_000, "rate_limited")
    rec = _Recorder(_response(200, {"id": "should-not-run"}))
    trace = HopTrace()

    status, payload = _run(vault, mgr, _chat(_PIN, strict=True), rec, trace)

    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == PIN_UNAVAILABLE
    assert payload["error"]["reason"] == "parked"
    assert trace.retry_after_s is not None
    assert 1 <= trace.retry_after_s <= 15
    assert rec.providers == []


def test_strict_open_circuit_is_pin_unavailable_without_retry_after(vault: KeyVault) -> None:
    groq = _groq(vault)
    _google(vault)
    mgr = FallbackManager(vault)
    # Per-key breaker (#106). The provider name is not the gate.
    breaker = get_circuit_breaker(groq.id)
    for _ in range(breaker.profile.failure_threshold):
        breaker.record_failure(status=500)
    assert breaker.state == "OPEN"
    rec = _Recorder(_response(200, {"id": "should-not-run"}))
    trace = HopTrace()

    status, payload = _run(vault, mgr, _chat(_PIN, strict=True), rec, trace)

    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == PIN_UNAVAILABLE
    assert payload["error"]["reason"] == "circuit_open"
    assert payload["served_provider"] is None
    assert trace.retry_after_s is None
    assert rec.providers == []


def test_strict_live_429_does_not_call_the_other_provider(vault: KeyVault) -> None:
    _groq(vault)
    _google(vault)
    mgr = FallbackManager(vault)
    limited = _response(429, {"error": "rate"})
    limited.headers = {"Retry-After": "12"}
    limited.text = "rate"
    rec = _Recorder(limited, _response(200, {"id": "google-should-not-run"}))
    trace = HopTrace()

    status, payload = _run(vault, mgr, _chat(_PIN, strict=True), rec, trace)

    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == PIN_UNAVAILABLE
    assert payload["error"]["reason"] == "parked"
    assert payload["served_model"] is None
    assert payload["served_provider"] is None
    assert trace.retry_after_s in (11, 12)
    assert rec.providers == ["groq"]


def test_strict_pin_matches_exact_catalog_id(vault: KeyVault) -> None:
    _groq(vault)
    mgr = FallbackManager(vault)
    rec = _Recorder(_response(200, {"id": "swapped"}))
    trace = HopTrace()

    status, payload = _run(vault, mgr, _chat("gpt-oss-120b", strict=True), rec, trace)

    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == PIN_UNAVAILABLE
    assert payload["error"]["reason"] == "no_hop"
    assert payload["served_provider"] is None
    assert rec.urls == []

    near = _Recorder(_response(200, {"id": "alias"}))
    alias_status, alias_payload = _run(vault, mgr, _chat("auto", strict=True), near)
    assert alias_status == 503
    assert isinstance(alias_payload, dict)
    assert alias_payload["error"]["reason"] == "not_in_catalog"
    assert near.urls == []


def test_non_strict_parked_pin_still_falls_through_and_reports_who_served(
    vault: KeyVault,
) -> None:
    groq = _groq(vault)
    _google(vault)
    mgr = FallbackManager(vault)
    mgr.record_park(groq.id, 30_000, "rate_limited")
    upstream = {"id": "chatcmpl-google", "model": "gemini-3.5-flash", "choices": []}
    rec = _Recorder(_response(200, upstream))
    trace = HopTrace()

    status, payload = _run(vault, mgr, _chat(_PIN), rec, trace)

    assert status == 200
    assert isinstance(payload, dict)
    assert payload["served_provider"] == "google"
    assert payload["served_model"] == _GOOGLE_MODEL
    assert payload["served_model"] != _PIN
    assert trace.provider == "google"
    assert trace.model_served == _GOOGLE_MODEL
    assert rec.providers == ["google"]
    assert rec.bodies[0]["model"] == _GOOGLE_MODEL
    assert "strict" not in rec.bodies[0]


def test_strict_false_matches_the_non_strict_fallback(vault: KeyVault) -> None:
    groq = _groq(vault)
    _google(vault)
    mgr = FallbackManager(vault)
    mgr.record_park(groq.id, 30_000, "rate_limited")
    rec = _Recorder(_response(200, {"id": "chatcmpl-google", "choices": []}))

    status, payload = _run(vault, mgr, _chat(_PIN, strict=False), rec)

    assert status == 200
    assert isinstance(payload, dict)
    assert payload["served_provider"] == "google"
    assert payload["served_model"] == _GOOGLE_MODEL
    assert rec.providers == ["google"]


def test_strict_uses_a_healthy_same_model_hop_and_skips_a_different_provider(
    vault: KeyVault,
) -> None:
    parked = _groq(vault, priority=0, secret="gsk-test-ov80-cccc")
    _groq(vault, priority=1, secret="gsk-test-ov80-dddd")
    _google(vault)
    mgr = FallbackManager(vault)
    mgr.record_park(parked.id, 30_000, "rate_limited")
    rec = _Recorder(_response(200, {"id": "chatcmpl-backup", "choices": []}))
    trace = HopTrace()

    status, payload = _run(vault, mgr, _chat(_PIN, strict=True), rec, trace)

    assert status == 200
    assert isinstance(payload, dict)
    assert payload["served_provider"] == "groq"
    assert payload["served_model"] == _PIN
    assert rec.providers == ["groq"]
    assert trace.model_served == _PIN


def test_strict_stream_park_returns_before_any_upstream_client(vault: KeyVault) -> None:
    groq = _groq(vault)
    _google(vault)
    mgr = FallbackManager(vault)
    mgr.record_park(groq.id, 8_000, "rate_limited")
    trace = HopTrace()

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("strict park must not open an upstream client")

    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", side_effect=_boom):
        status, payload = asyncio.run(
            prepare_chat_stream(
                vault, mgr, {**_chat(_PIN, strict=True), "stream": True}, trace=trace
            )
        )

    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == PIN_UNAVAILABLE
    assert payload["served_provider"] is None
    assert payload["served_model"] is None
    assert trace.retry_after_s is not None


def test_http_strict_header_sets_retry_after_and_does_not_call_google(
    vault: KeyVault, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENVAULT_LOCAL_BASE_URL", raising=False)
    _groq(vault)
    _google(vault)
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    client = TestClient(app, client=("127.0.0.1", 5555))
    _identity, headers = issue_key(client, tier="free")
    limited = _response(429, {"error": "rate"})
    limited.headers = {"Retry-After": "9"}
    limited.text = "rate"
    rec = _Recorder(limited, _response(200, {"id": "google", "choices": []}))
    body = {"model": _PIN, "messages": [{"role": "user", "content": "hi"}]}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=rec.mock):
        first = client.post(
            "/v1/chat/completions",
            json=body,
            headers={**headers, STRICT_PIN_HEADER: "true"},
        )
        second = client.post(
            "/v1/chat/completions",
            json={**body, "strict": True},
            headers=headers,
        )

    assert first.status_code == 503
    assert first.json()["error"]["type"] == PIN_UNAVAILABLE
    assert first.json()["served_provider"] is None
    assert first.json()["served_model"] is None
    assert first.headers.get("Retry-After") in {"8", "9"}
    assert second.status_code == 503
    assert second.json()["error"]["type"] == PIN_UNAVAILABLE
    assert second.json()["error"]["reason"] == "parked"
    assert second.headers.get("Retry-After")
    assert int(second.headers["Retry-After"]) >= 1
    assert rec.providers == ["groq"]


def test_strict_together_and_groq_serves_groq_only(vault: KeyVault) -> None:
    _together(vault, priority=0)
    _groq(vault, priority=10)
    mgr = FallbackManager(vault)
    upstream = {
        "id": "chatcmpl-groq",
        "model": "upstream-name",
        "choices": [{"message": {"content": "ok"}}],
    }
    rec = _Recorder(_response(200, upstream))
    trace = HopTrace()

    status, payload = _run(vault, mgr, _chat(_PIN, strict=True), rec, trace)

    assert status == 200
    assert isinstance(payload, dict)
    assert payload["served_provider"] == "groq"
    assert payload["served_model"] == _PIN
    assert payload["served_local"] is False
    assert trace.provider == "groq"
    assert trace.model_served == _PIN
    assert rec.providers == ["groq"]
    assert rec.bodies[0]["model"] == _PIN
    assert "strict" not in rec.bodies[0]


def test_strict_together_without_groq_is_pin_unavailable_no_hop(vault: KeyVault) -> None:
    _together(vault)
    mgr = FallbackManager(vault)
    rec = _Recorder(_response(200, {"id": "together-should-not-run"}))
    trace = HopTrace()

    status, payload = _run(vault, mgr, _chat(_PIN, strict=True), rec, trace)

    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == PIN_UNAVAILABLE
    assert payload["error"]["reason"] == "no_hop"
    assert payload["error"]["model"] == _PIN
    assert payload["served_provider"] is None
    assert payload["served_model"] is None
    assert trace.error_type == PIN_UNAVAILABLE
    assert rec.urls == []
    assert rec.providers == []


def test_strict_parked_groq_does_not_serve_healthy_together(vault: KeyVault) -> None:
    groq = _groq(vault, priority=10)
    _together(vault, priority=0)
    mgr = FallbackManager(vault)
    mgr.record_park(groq.id, 30_000, "rate_limited")
    rec = _Recorder(_response(200, {"id": "together-should-not-run"}))
    trace = HopTrace()

    status, payload = _run(vault, mgr, _chat(_PIN, strict=True), rec, trace)

    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == PIN_UNAVAILABLE
    assert payload["error"]["reason"] == "parked"
    assert payload["served_provider"] is None
    assert trace.retry_after_s is not None
    assert rec.urls == []


def test_non_strict_same_model_still_follows_fallback_order(vault: KeyVault) -> None:
    _together(vault, priority=0)
    _groq(vault, priority=10)
    mgr = FallbackManager(vault)
    rec = _Recorder(_response(200, {"id": "chatcmpl-together", "choices": []}))
    trace = HopTrace()

    status, payload = _run(vault, mgr, _chat(_PIN), rec, trace)

    assert status == 200
    assert isinstance(payload, dict)
    assert payload["served_provider"] == "together"
    assert payload["served_model"] == _PIN
    assert trace.provider == "together"
    assert trace.model_served == _PIN
    assert rec.providers == ["together"]
    assert rec.bodies[0]["model"] == _PIN
    assert "strict" not in rec.bodies[0]


def test_strict_not_in_catalog_unchanged_when_together_and_groq_present(
    vault: KeyVault,
) -> None:
    _together(vault, priority=0)
    _groq(vault, priority=10)
    mgr = FallbackManager(vault)
    rec = _Recorder(_response(200, {"id": "should-not-run"}))

    status, payload = _run(vault, mgr, _chat("auto", strict=True), rec)

    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == PIN_UNAVAILABLE
    assert payload["error"]["reason"] == "not_in_catalog"
    assert payload["error"]["model"] == "auto"
    assert payload["served_provider"] is None
    assert rec.urls == []


def test_http_strict_served_provider_header_is_groq(
    vault: KeyVault, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENVAULT_LOCAL_BASE_URL", raising=False)
    _together(vault, priority=0)
    _groq(vault, priority=10)
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    client = TestClient(app, client=("127.0.0.1", 5555))
    _identity, headers = issue_key(client, tier="free")
    rec = _Recorder(
        _response(200, {"id": "chatcmpl-groq", "choices": [{"message": {"content": "ok"}}]})
    )
    body = {"model": _PIN, "messages": [{"role": "user", "content": "hi"}]}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=rec.mock):
        response = client.post(
            "/v1/chat/completions",
            json=body,
            headers={**headers, STRICT_PIN_HEADER: "true"},
        )

    assert response.status_code == 200
    assert response.headers.get(SERVED_PROVIDER_HEADER) == "groq"
    assert response.headers.get("x-openvault-served-provider") == "groq"
    assert response.json()["served_provider"] == "groq"
    assert response.json()["served_model"] == _PIN
    assert rec.providers == ["groq"]
