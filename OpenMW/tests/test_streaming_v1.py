"""FreeRoute streaming /v1/chat/completions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.proxy import prepare_chat_stream
from openmw.openvault.vault.store import KeyVault


@pytest.fixture()
def vault(tmp_path: Path) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


def test_stream_without_keys_returns_json_error(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    client = TestClient(app)
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

    chunks = [b"data: {\"id\":\"1\"}\n\n", b"data: [DONE]\n\n"]

    class _FakeResp:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

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

    async def _fake_prepare(
        *_args: Any, **_kwargs: Any
    ) -> tuple[int, AsyncIterator[bytes]]:
        async def _gen() -> AsyncIterator[bytes]:
            yield b"data: hello\n\n"
            yield b"data: [DONE]\n\n"

        return 200, _gen()

    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    with patch(
        "openmw.openvault.app.prepare_chat_stream",
        new=AsyncMock(side_effect=_fake_prepare),
    ):
        client = TestClient(app)
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
