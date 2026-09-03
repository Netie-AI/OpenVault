"""FreeRoute streaming /v1/chat/completions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.proxy import prepare_chat_stream
from openmw.openvault.vault.ratelimit import TokenBudgetLimiter
from openmw.openvault.vault.store import KeyVault


class _FreezeClock:
    """Pin limiter refill so spend assertions are not erased by wall time."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def vault(tmp_path: Path) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


def test_stream_without_keys_returns_json_error(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    client = TestClient(app, client=("127.0.0.1", 5555))
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["type"] == "openvault_no_keys"
    assert "stream not supported" not in resp.text


def test_prepare_chat_stream_yields_upstream_bytes(vault: KeyVault) -> None:
    rec = vault.create(
        label="stream-hop",
        provider="openai",
        secret="sk-stream-test-aaaaaaaa",
        role="primary",
        base_url="https://example.invalid/v1",
        priority=1,
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)
    fallback = FallbackManager(vault)

    chunks = [b'data: {"id":"1"}\n\n', b"data: [DONE]\n\n"]

    class _FakeResp:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {"content-type": "text/event-stream"}

        async def aiter_bytes(self) -> AsyncIterator[bytes]:
            for c in chunks:
                yield c

        async def aclose(self) -> None:
            return None

        async def aread(self) -> bytes:
            return b""

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def build_request(self, *args: Any, **kwargs: Any) -> object:
            return object()

        async def send(self, request: object, *, stream: bool = False) -> _FakeResp:
            assert stream is True
            return _FakeResp()

        async def aclose(self) -> None:
            return None

    async def _run() -> None:
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", _FakeClient):
            status, result = await prepare_chat_stream(
                vault,
                fallback,
                {"model": "gpt-test", "messages": [{"role": "user", "content": "x"}]},
            )
        assert status == 200
        assert not isinstance(result, dict)
        got = b"".join([chunk async for chunk in result])
        assert got == b"".join(chunks)

    asyncio.run(_run())


def test_gateway_stream_success_path(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    rec = vault.create(
        label="stream-hop",
        provider="openai",
        secret="sk-stream-test-bbbbbbbb",
        role="primary",
        base_url="https://example.invalid/v1",
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)

    async def _fake_prepare(*_args: Any, **_kwargs: Any) -> tuple[int, AsyncIterator[bytes]]:
        async def _gen() -> AsyncIterator[bytes]:
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"

        return 200, _gen()

    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    with patch(
        "openmw.openvault.app.prepare_chat_stream",
        new=AsyncMock(side_effect=_fake_prepare),
    ):
        client = TestClient(app, client=("127.0.0.1", 5555))
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "auto",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in (resp.headers.get("content-type") or "")
            body = b"".join(resp.iter_bytes())
            assert b"data: hello" in body
            assert b"[DONE]" in body


def _stream_with_usage_chunks() -> AsyncIterator[bytes]:
    async def _gen() -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        yield (
            b'data: {"choices":[],"usage":{"prompt_tokens":12,'
            b'"completion_tokens":8,"total_tokens":20}}\n\n'
        )
        yield b"data: [DONE]\n\n"

    return _gen()


def test_gateway_stream_settles_from_include_usage(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When client asks for stream usage, refund reserved-minus-actual."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    rec = vault.create(
        label="stream-hop",
        provider="openai",
        secret="sk-stream-test-cccccccc",
        role="primary",
        base_url="https://example.invalid/v1",
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)

    async def _fake_prepare(*_args: Any, **_kwargs: Any) -> tuple[int, AsyncIterator[bytes]]:
        return 200, _stream_with_usage_chunks()

    # Freeze refill — free tier refills ~666 tok/s and can erase a 20-tok charge.
    clock = _FreezeClock()
    limiter = TokenBudgetLimiter(clock=clock)
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        rate_limiter=limiter,
    )
    client = TestClient(app, client=("127.0.0.1", 5555))
    identity, headers = issue_key(client)
    before = client.get(
        "/api/freeroute/ratelimit", params={"tier": "free", "identity": identity}
    ).json()["token_remaining"]

    with (
        patch(
            "openmw.openvault.app.prepare_chat_stream",
            new=AsyncMock(side_effect=_fake_prepare),
        ),
        client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "auto",
                "stream": True,
                "max_tokens": 500,
                "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers=headers,
        ) as resp,
    ):
        assert resp.status_code == 200
        _ = b"".join(resp.iter_bytes())

    after = client.get(
        "/api/freeroute/ratelimit", params={"tier": "free", "identity": identity}
    ).json()["token_remaining"]
    spent = before - after
    assert spent < 100, f"should refund toward actual 20, not keep ~501 reserve; spent={spent}"
    assert spent >= 1, f"should charge some tokens; spent={spent}"


def test_gateway_stream_keeps_reservation_when_usage_absent(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """include_usage asked but upstream never sent usage -> keep reservation."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    rec = vault.create(
        label="stream-hop",
        provider="openai",
        secret="sk-stream-test-dddddddd",
        role="primary",
        base_url="https://example.invalid/v1",
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)

    async def _fake_prepare(*_args: Any, **_kwargs: Any) -> tuple[int, AsyncIterator[bytes]]:
        async def _gen() -> AsyncIterator[bytes]:
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"

        return 200, _gen()

    clock = _FreezeClock()
    limiter = TokenBudgetLimiter(clock=clock)
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        rate_limiter=limiter,
    )
    client = TestClient(app, client=("127.0.0.1", 5555))
    identity, headers = issue_key(client)
    before = client.get(
        "/api/freeroute/ratelimit", params={"tier": "free", "identity": identity}
    ).json()["token_remaining"]

    with (
        patch(
            "openmw.openvault.app.prepare_chat_stream",
            new=AsyncMock(side_effect=_fake_prepare),
        ),
        client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "auto",
                "stream": True,
                "max_tokens": 500,
                "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers=headers,
        ) as resp,
    ):
        assert resp.status_code == 200
        _ = b"".join(resp.iter_bytes())

    after = client.get(
        "/api/freeroute/ratelimit", params={"tier": "free", "identity": identity}
    ).json()["token_remaining"]
    spent = before - after
    # Conservative keep-reservation (~prompt+500); clock frozen so refill cannot eat it.
    assert spent > 400, f"expected keep-reservation (~501), spent={spent}"


def test_gateway_stream_ignores_usage_without_include_usage(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Usage in SSE without stream_options.include_usage -> keep reservation."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    rec = vault.create(
        label="stream-hop",
        provider="openai",
        secret="sk-stream-test-eeeeeeee",
        role="primary",
        base_url="https://example.invalid/v1",
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)

    async def _fake_prepare(*_args: Any, **_kwargs: Any) -> tuple[int, AsyncIterator[bytes]]:
        return 200, _stream_with_usage_chunks()

    clock = _FreezeClock()
    limiter = TokenBudgetLimiter(clock=clock)
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        rate_limiter=limiter,
    )
    client = TestClient(app, client=("127.0.0.1", 5555))
    identity, headers = issue_key(client)
    before = client.get(
        "/api/freeroute/ratelimit", params={"tier": "free", "identity": identity}
    ).json()["token_remaining"]

    with (
        patch(
            "openmw.openvault.app.prepare_chat_stream",
            new=AsyncMock(side_effect=_fake_prepare),
        ),
        client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "auto",
                "stream": True,
                "max_tokens": 500,
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers=headers,
        ) as resp,
    ):
        assert resp.status_code == 200
        _ = b"".join(resp.iter_bytes())

    after = client.get(
        "/api/freeroute/ratelimit", params={"tier": "free", "identity": identity}
    ).json()["token_remaining"]
    spent = before - after
    assert spent > 400, f"without include_usage must keep reservation, spent={spent}"
    assert spent > 100, "must not settle to the stream usage total of 20"
