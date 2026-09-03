"""Exact-match response cache (OmniRoute-class, temperature 0 only)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from openmw.openvault.route.key_rotator import reset_all as reset_rotator
from openmw.openvault.route.response_cache import cache_key, cacheable, lookup, reset, store
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.proxy import chat_completions
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.usage_store import HopTrace


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reset()
    reset_rotator()
    yield
    reset()
    reset_rotator()


def _body(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0,
    }
    payload.update(extra)
    return payload


def test_not_cacheable_without_explicit_zero() -> None:
    assert cacheable(_body()) is True
    assert cacheable(_body(temperature=1)) is False
    body = _body()
    del body["temperature"]
    assert cacheable(body) is False
    assert cacheable(_body(stream=True)) is False


def test_tenants_do_not_share_entries() -> None:
    payload = {"id": "chatcmpl-a", "choices": [{"message": {"content": "ok"}}]}
    store("tenant-a", _body(), payload)
    assert lookup("tenant-a", _body())["id"] == "chatcmpl-a"
    assert lookup("tenant-b", _body()) is None
    assert cache_key("tenant-a", _body()) != cache_key("tenant-b", _body())


def test_lookup_returns_a_copy() -> None:
    payload = {"id": "chatcmpl-a", "choices": []}
    store("t", _body(), payload)
    hit = lookup("t", _body())
    assert hit is not None
    hit["id"] = "mutated"
    assert lookup("t", _body())["id"] == "chatcmpl-a"


@pytest.fixture()
def vault(tmp_path: Any) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


def _ok_client(payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = '{"id":"chatcmpl-ok"}'
    resp.headers = {}
    resp.json = MagicMock(return_value=payload)
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def test_proxy_second_identical_temp0_is_a_cache_hit(vault: KeyVault) -> None:
    key = vault.create(
        label="openai",
        provider="openai",
        secret="sk-test-cache",
        role="primary",
        base_url="https://api.openai.com/v1",
    )
    vault.set_precheck(key.id, status="ok", latency_ms=1.0, error=None)
    mgr = FallbackManager(vault)
    client = _ok_client({"id": "chatcmpl-ok", "choices": [], "usage": {"total_tokens": 9}})

    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=client):
        first_trace = HopTrace()
        status, payload = asyncio.run(
            chat_completions(vault, mgr, _body(), trace=first_trace, tenant="ov_a")
        )
        assert status == 200
        assert first_trace.cache_hit is False
        assert client.post.await_count == 1

        second_trace = HopTrace()
        status2, payload2 = asyncio.run(
            chat_completions(vault, mgr, _body(), trace=second_trace, tenant="ov_a")
        )
        assert status2 == 200
        assert second_trace.cache_hit is True
        assert payload2["id"] == payload["id"]
        assert client.post.await_count == 1

        other = HopTrace()
        asyncio.run(chat_completions(vault, mgr, _body(), trace=other, tenant="ov_b"))
        assert other.cache_hit is False
        assert client.post.await_count == 2
