"""LOCAL-1: loopback Qwen hop, served_* fields, fail-closed local_only.

Stub transport only. No live key, no network, no skip.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.fernet import Fernet

from openmw.openvault.route.breaker import reset_all_circuit_breakers
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.local_hop import (
    LOCAL_ONLY_ERROR_TYPE,
    REASON_MODEL_NOT_LOADED,
    REASON_NOT_LOOPBACK,
    REASON_UNREACHABLE,
    is_loopback_base_url,
    local_status_hop,
    pop_local_only,
    probe_local,
)
from openmw.openvault.vault.providers import (
    DEFAULT_LOCAL_MODEL,
    LOCAL_QWEN_ID,
    get_provider,
    spendable_for_freeroute,
)
from openmw.openvault.vault.proxy import chat_completions
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.usage_store import HopTrace

_CHAT = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    reset_all_circuit_breakers()
    yield
    reset_all_circuit_breakers()


@pytest.fixture()
def vault(tmp_path: Any) -> KeyVault:
    return KeyVault(db_path=tmp_path / "keys.db", seal=Seal(Fernet.generate_key()))


def _openai_record(vault: KeyVault) -> Any:
    rec = vault.create(
        label="cloud-openai",
        provider="openai",
        secret="sk-test-local1-cloud-aaaa",
        role="primary",
        priority=1,
        base_url="https://api.openai.com/v1",
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)
    return rec


def _ok_chat_payload(*, model: str, text: str = "ok") -> dict[str, Any]:
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _mock_async_client(handler: Any) -> Any:
    transport = httpx.MockTransport(handler)

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return httpx.AsyncClient(*args, **kwargs)

    return factory


def test_local_qwen_registered_without_cloud_key(vault: KeyVault) -> None:
    spec = get_provider(LOCAL_QWEN_ID)
    assert spec is not None
    row = spec.to_dict()
    assert row["spendable"] is True
    assert row["local"] is True
    assert row["openai_compatible"] is True
    assert DEFAULT_LOCAL_MODEL in row["chat_models"]
    assert vault.list_keys() == []
    ids = {r["id"] for r in spendable_for_freeroute()}
    assert LOCAL_QWEN_ID in ids
    assert all(r["local"] is False for r in spendable_for_freeroute() if r["id"] != LOCAL_QWEN_ID)


def test_refuse_non_loopback_base_url() -> None:
    assert is_loopback_base_url("http://127.0.0.1:8080/v1") is True
    assert is_loopback_base_url("http://[::1]:8080/v1") is True
    assert is_loopback_base_url("http://8.8.8.8:8080/v1") is False
    assert is_loopback_base_url("http://example.com/v1") is False
    assert is_loopback_base_url("http://192.168.1.10:8080/v1") is False
    # localhost is allowed only when getaddrinfo is all-loopback (typical /etc/hosts).
    assert isinstance(is_loopback_base_url("http://localhost:8080/v1"), bool)


def test_non_loopback_env_refuses_without_http(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_LOCAL_BASE_URL", "http://8.8.8.8:8080/v1")
    _openai_record(vault)
    mgr = FallbackManager(vault)
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, json=_ok_chat_payload(model="should-not-run"))

    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", _mock_async_client(handler)):
        status, payload = asyncio.run(chat_completions(vault, mgr, {**_CHAT, "local_only": True}))
    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == LOCAL_ONLY_ERROR_TYPE
    assert payload["error"]["reason"] == REASON_NOT_LOOPBACK
    assert hits == []


def test_served_fields_from_actual_cloud_hop(vault: KeyVault) -> None:
    """served_* come from the hop that served, not from a Qwen-shaped requested id."""
    _openai_record(vault)
    mgr = FallbackManager(vault)
    posted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(str(request.url))
        assert "api.openai.com" in str(request.url)
        return httpx.Response(200, json=_ok_chat_payload(model="upstream-will-lie-qwen2.5:0.5b"))

    body = {
        "model": "qwen2.5:0.5b",
        "messages": [{"role": "user", "content": "hi"}],
    }
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", _mock_async_client(handler)):
        status, payload = asyncio.run(chat_completions(vault, mgr, body))
    assert status == 200
    assert isinstance(payload, dict)
    assert payload["served_provider"] == "openai"
    assert payload["served_local"] is False
    assert payload["served_model"] == "gpt-5.6-sol"
    assert payload["served_model"] != "qwen2.5:0.5b"
    assert posted


def test_local_only_down_zero_cloud_attempts(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_LOCAL_BASE_URL", "http://127.0.0.1:8080/v1")
    _openai_record(vault)
    mgr = FallbackManager(vault)
    cloud_posts = 0
    local_posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cloud_posts, local_posts
        url = str(request.url)
        if "api.openai.com" in url:
            cloud_posts += 1
            return httpx.Response(200, json=_ok_chat_payload(model="gpt-5.6-sol"))
        local_posts += 1
        raise httpx.ConnectError("local down", request=request)

    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", _mock_async_client(handler)):
        status, payload = asyncio.run(chat_completions(vault, mgr, {**_CHAT, "local_only": True}))
    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == LOCAL_ONLY_ERROR_TYPE
    assert payload["error"]["reason"] == REASON_UNREACHABLE
    assert cloud_posts == 0
    assert local_posts >= 1


def test_local_only_up_served_local_true(vault: KeyVault, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_LOCAL_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("OPENVAULT_LOCAL_MODEL", DEFAULT_LOCAL_MODEL)
    _openai_record(vault)
    mgr = FallbackManager(vault)
    cloud_posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cloud_posts
        url = str(request.url)
        if "api.openai.com" in url:
            cloud_posts += 1
            return httpx.Response(200, json=_ok_chat_payload(model="gpt-5.6-sol"))
        body = json_body(request)
        assert "local_only" not in body
        return httpx.Response(200, json=_ok_chat_payload(model=DEFAULT_LOCAL_MODEL, text="local"))

    trace = HopTrace()
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", _mock_async_client(handler)):
        status, payload = asyncio.run(
            chat_completions(vault, mgr, {**_CHAT, "local_only": True}, trace=trace)
        )
    assert status == 200
    assert isinstance(payload, dict)
    assert payload["served_local"] is True
    assert payload["served_provider"] == LOCAL_QWEN_ID
    assert payload["served_model"] == DEFAULT_LOCAL_MODEL
    assert trace.served_local is True
    assert cloud_posts == 0


def test_local_only_stripped_before_upstream(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_LOCAL_BASE_URL", "http://127.0.0.1:8080/v1")
    mgr = FallbackManager(vault)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json_body(request))
        return httpx.Response(200, json=_ok_chat_payload(model=DEFAULT_LOCAL_MODEL))

    original = {**_CHAT, "local_only": True}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", _mock_async_client(handler)):
        status, _payload = asyncio.run(chat_completions(vault, mgr, original))
    assert status == 200
    assert original["local_only"] is True
    assert seen
    for body in seen:
        assert "local_only" not in body


def test_pop_local_only_only_json_true() -> None:
    body = {"local_only": True, "model": "auto"}
    assert pop_local_only(body) is True
    assert "local_only" not in body
    body_false = {"local_only": False}
    assert pop_local_only(body_false) is False
    assert "local_only" not in body_false
    body_one = {"local_only": 1}
    assert pop_local_only(body_one) is False


def test_arming_reason_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_LOCAL_BASE_URL", "http://127.0.0.1:8080/v1")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = probe_local(client=client)
    assert result.reason == REASON_UNREACHABLE
    hop = local_status_hop(probe=result)
    assert hop["local"] is True
    assert hop["local_reason"] == REASON_UNREACHABLE
    assert hop["provider"] == LOCAL_QWEN_ID
    assert hop["key_id"] == "local:local_qwen"


def test_arming_reason_model_not_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_LOCAL_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("OPENVAULT_LOCAL_MODEL", DEFAULT_LOCAL_MODEL)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "llama3.2:1b"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = probe_local(client=client)
    assert result.reason == REASON_MODEL_NOT_LOADED
    hop = local_status_hop(probe=result)
    assert hop["local_reason"] == REASON_MODEL_NOT_LOADED


def test_arming_reason_not_loopback_without_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_LOCAL_BASE_URL", "https://example.invalid/v1")

    class Boom:
        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("non-loopback must not be contacted")

        def close(self) -> None:
            return None

    result = probe_local(client=Boom())  # type: ignore[arg-type]
    assert result.reason == REASON_NOT_LOOPBACK


def test_existing_empty_pool_still_503_no_keys(vault: KeyVault) -> None:
    mgr = FallbackManager(vault)
    status, payload = asyncio.run(chat_completions(vault, mgr, dict(_CHAT)))
    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == "openvault_no_keys"


def test_fallback_manager_arming_unchanged(vault: KeyVault) -> None:
    """Synthetic local hop is not a vault key and must not enter ordered_candidates."""
    rec = _openai_record(vault)
    mgr = FallbackManager(vault)
    ordered = mgr.ordered_candidates()
    assert [r.id for r in ordered] == [rec.id]
    shown = {hop["key_id"] for hop in mgr.status().hops}
    assert rec.id in shown
    assert "local:local_qwen" not in shown


def test_existing_cloud_429_park_still_holds(vault: KeyVault) -> None:
    """Existing hop circuit/park behaviour is untouched by LOCAL-1."""
    import time

    from openmw.openvault.route.attempt import classify_attempt

    primary = vault.create(
        label="primary",
        provider="openai",
        secret="sk-test-local1-park-aaaa",
        role="primary",
        priority=1,
    )
    backup = vault.create(
        label="backup",
        provider="openai",
        secret="sk-test-local1-park-bbbb",
        role="backup",
        priority=1,
    )
    mgr = FallbackManager(vault)
    for _ in range(3):
        outcome = classify_attempt(429, "too many requests")
        mgr.record_park(primary.id, outcome.cooldown_ms, outcome.reason or "rate_limited")
    circ = mgr._circuit(primary.id)
    assert circ.state == "closed"
    assert circ.failures == 0
    assert circ.park_until is not None
    assert circ.park_until > time.time()
    assert [r.id for r in mgr.ordered_candidates()] == [backup.id]


def test_status_marks_local_hop(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from fastapi.testclient import TestClient

    from openmw.openvault.app import create_app

    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    client = TestClient(app, client=("127.0.0.1", 5555))
    body = client.get("/api/freeroute/status").json()
    spendable = {row["id"]: row for row in body["spendable"]}
    assert spendable[LOCAL_QWEN_ID]["local"] is True
    assert spendable["groq"]["local"] is False
    hops = [h for h in body["hops"] if h.get("local")]
    assert len(hops) == 1
    assert hops[0]["provider"] == LOCAL_QWEN_ID
    assert hops[0]["local_reason"] == REASON_UNREACHABLE


def json_body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode("utf-8") or "{}")


def test_cloud_magicmock_path_stamps_served_local_false(vault: KeyVault) -> None:
    """Same stub style as test_attempt_policy: cloud hop served_local is false."""
    _openai_record(vault)
    mgr = FallbackManager(vault)
    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.text = '{"id":"chatcmpl-ok"}'
    resp_ok.headers = {}
    resp_ok.json = MagicMock(return_value={"id": "chatcmpl-ok", "choices": []})
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=resp_ok)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock_client):
        status, payload = asyncio.run(chat_completions(vault, mgr, dict(_CHAT)))
    assert status == 200
    assert payload["served_local"] is False
    assert payload["served_provider"] == "openai"
    posted = mock_client.post.await_args
    assert posted is not None
    assert "local_only" not in posted.kwargs["json"]
