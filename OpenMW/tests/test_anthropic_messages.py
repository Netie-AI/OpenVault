"""Anthropic Messages hop — OpenAI /v1 in, native Messages upstream."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from openmw.openvault.route.key_rotator import reset_all as reset_rotator
from openmw.openvault.route.response_cache import reset as reset_cache
from openmw.openvault.vault.anthropic_messages import (
    messages_to_openai_chat,
    messages_url,
    openai_chat_to_messages,
    openai_sse_from_anthropic,
    request_headers,
)
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.proxy import chat_completions
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.usage_store import HopTrace


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_cache()
    reset_rotator()
    yield
    reset_cache()
    reset_rotator()


def test_messages_url_uses_v1_on_catalog_base() -> None:
    assert messages_url("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"
    assert messages_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1/messages"


def test_headers_are_anthropic_not_bearer() -> None:
    headers = request_headers("sk-ant-test")
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["anthropic-beta"] == "prompt-caching-2024-07-31"
    assert "Authorization" not in headers


def test_system_and_user_split_and_max_tokens_default() -> None:
    body = openai_chat_to_messages(
        {
            "model": "claude-opus-4",
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hi"},
            ],
        }
    )
    assert body["model"] == "claude-opus-4"
    assert body["system"][0]["text"] == "be brief"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["max_tokens"] == 1024


def test_no_system_omits_cache_control() -> None:
    body = openai_chat_to_messages(
        {"model": "claude-opus-4", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert "system" not in body


def test_response_maps_usage_without_inventing() -> None:
    openai = messages_to_openai_chat(
        {
            "id": "msg_1",
            "model": "claude-opus-4",
            "content": [{"type": "text", "text": "hello"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }
    )
    assert openai["choices"][0]["message"]["content"] == "hello"
    assert openai["usage"]["prompt_tokens"] == 4
    assert openai["usage"]["completion_tokens"] == 2
    assert openai["usage"]["total_tokens"] == 6
    assert "cache_read_input_tokens" not in openai["usage"]


def test_observed_cache_tokens_are_copied_not_invented() -> None:
    openai = messages_to_openai_chat(
        {
            "id": "msg_2",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 1,
                "cache_read_input_tokens": 8,
                "cache_creation_input_tokens": 2,
            },
        }
    )
    assert openai["usage"]["cache_read_input_tokens"] == 8
    assert openai["usage"]["cache_creation_input_tokens"] == 2
    assert openai["usage"]["prompt_tokens_details"]["cached_tokens"] == 8


@pytest.fixture()
def vault(tmp_path: Any) -> KeyVault:
    return KeyVault(db_path=tmp_path / "keys.db", seal=Seal(Fernet.generate_key()))


def test_proxy_posts_messages_not_chat_completions(vault: KeyVault) -> None:
    key = vault.create(
        label="claude",
        provider="anthropic",
        secret="sk-ant-test",
        role="primary",
        base_url="https://api.anthropic.com",
    )
    vault.set_precheck(key.id, status="ok", latency_ms=1.0, error=None)
    mgr = FallbackManager(vault)

    resp = MagicMock()
    resp.status_code = 200
    resp.text = "{}"
    resp.headers = {}
    resp.json = MagicMock(
        return_value={
            "id": "msg_ok",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-4",
            "content": [{"type": "text", "text": "yo"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=client):
        status, payload = asyncio.run(
            chat_completions(
                vault,
                mgr,
                {
                    "model": "claude-opus-4",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                trace=HopTrace(),
            )
        )

    assert status == 200
    assert payload["choices"][0]["message"]["content"] == "yo"
    posted = client.post.await_args
    assert posted is not None
    assert posted.args[0] == "https://api.anthropic.com/v1/messages"
    assert posted.kwargs["headers"]["x-api-key"] == "sk-ant-test"
    assert posted.kwargs["json"]["messages"][0]["content"] == "hi"
    assert "chat/completions" not in posted.args[0]


def test_sse_translator_emits_openai_chunks() -> None:
    async def _run() -> bytes:
        raw = (
            b"event: message_start\n"
            b'data: {"type":"message_start","message":{"id":"msg_x","usage":{"input_tokens":2}}}\n\n'
            b"event: content_block_delta\n"
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n'
            b"event: message_delta\n"
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'
            b"event: message_stop\n"
            b'data: {"type":"message_stop"}\n\n'
        )

        async def _chunks() -> Any:
            yield raw

        out = b""
        async for piece in openai_sse_from_anthropic(_chunks()):
            out += piece
        return out

    blob = asyncio.run(_run()).decode()
    assert "chat.completion.chunk" in blob
    assert "Hi" in blob
    assert "[DONE]" in blob
    assert "prompt_tokens" in blob
