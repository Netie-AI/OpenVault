"""Anthropic Messages API — OpenAI-shaped /v1 in, native Messages out.

OmniRoute and managed gateways speak ``POST /v1/messages``. FreeRoute used to
skip every anthropic hop (\"anthropic chat not via /v1 proxy yet\"), so a
healthy vaulted Claude key looked dead. This module translates; it does not
invent a model id, a price, or a token count.

``max_tokens`` is required upstream. When the OpenAI-shaped body omitted it we
send 1024 (same default the gateway already reserves). Catalog ``chat_models``
are pinned from Anthropic's own model list; ``auto`` resolves to the first
pinned id rather than inventing one.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

ANTHROPIC_VERSION = "2023-06-01"
PROMPT_CACHING_BETA = "prompt-caching-2024-07-31"
DEFAULT_MAX_TOKENS = 1024

_STOP_TO_FINISH = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
}


def messages_url(base: str) -> str:
    root = (base or "").rstrip("/")
    if root.endswith("/v1"):
        return f"{root}/messages"
    return f"{root}/v1/messages"


def request_headers(secret: str, *, stream: bool = False) -> dict[str, str]:
    headers = {
        "x-api-key": secret,
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": PROMPT_CACHING_BETA,
        "Content-Type": "application/json",
    }
    if stream:
        headers["Accept"] = "text/event-stream"
    return headers


def openai_chat_to_messages(body: dict[str, Any]) -> dict[str, Any]:
    """OpenAI chat.completions JSON -> Anthropic Messages JSON."""
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        content = message.get("content")
        if role in ("system", "developer"):
            text = _plain_text(content)
            if text:
                system_parts.append(text)
            continue
        mapped_role = "assistant" if role == "assistant" else "user"
        converted.append({"role": mapped_role, "content": _anthropic_content(content)})

    merged: list[dict[str, Any]] = []
    for item in converted:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["content"] = _concat_content(merged[-1]["content"], item["content"])
        else:
            merged.append(item)
    if merged and merged[0]["role"] == "assistant":
        merged.insert(0, {"role": "user", "content": "."})
    if not merged:
        merged = [{"role": "user", "content": "."}]

    max_tokens = body.get("max_tokens")
    if max_tokens is None:
        max_tokens = body.get("max_completion_tokens")
    try:
        max_tokens_i = int(max_tokens) if max_tokens is not None else DEFAULT_MAX_TOKENS
    except (TypeError, ValueError):
        max_tokens_i = DEFAULT_MAX_TOKENS
    max_tokens_i = max(1, max_tokens_i)

    out: dict[str, Any] = {
        "model": body.get("model"),
        "max_tokens": max_tokens_i,
        "messages": merged,
    }
    if system_parts:
        # Stable system prefix: mark cache_control. We do not invent a hit —
        # Anthropic reports cache_read / cache_creation on the usage object.
        out["system"] = [
            {
                "type": "text",
                "text": "\n\n".join(system_parts),
                "cache_control": {"type": "ephemeral"},
            }
        ]
    if body.get("temperature") is not None:
        out["temperature"] = body.get("temperature")
    if body.get("stream"):
        out["stream"] = True
    stop = body.get("stop")
    if isinstance(stop, str) and stop:
        out["stop_sequences"] = [stop]
    elif isinstance(stop, list) and stop:
        out["stop_sequences"] = [str(s) for s in stop if str(s)]
    return out


def messages_to_openai_chat(body: dict[str, Any]) -> dict[str, Any]:
    """Anthropic message object -> OpenAI chat.completion object."""
    if not isinstance(body, dict):
        return {"id": "chatcmpl-anthropic", "choices": [], "usage": {}}
    if isinstance(body.get("choices"), list):
        return body
    text_parts: list[str] = []
    for block in body.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
    stop = str(body.get("stop_reason") or "end_turn")
    usage_in = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    prompt = _int_or_zero(usage_in.get("input_tokens"))
    completion = _int_or_zero(usage_in.get("output_tokens"))
    usage: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if "cache_read_input_tokens" in usage_in:
        cached = _int_or_zero(usage_in.get("cache_read_input_tokens"))
        usage["cache_read_input_tokens"] = cached
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    if "cache_creation_input_tokens" in usage_in:
        usage["cache_creation_input_tokens"] = _int_or_zero(
            usage_in.get("cache_creation_input_tokens")
        )
    return {
        "id": str(body.get("id") or "chatcmpl-anthropic"),
        "object": "chat.completion",
        "model": str(body.get("model") or ""),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "".join(text_parts)},
                "finish_reason": _STOP_TO_FINISH.get(stop, "stop"),
            }
        ],
        "usage": usage,
    }


async def openai_sse_from_anthropic(
    chunks: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """Rewrite Anthropic SSE into OpenAI chat.completion.chunk SSE."""
    carry = ""
    msg_id = "chatcmpl-anthropic"
    prompt_tokens = 0
    completion_tokens = 0
    async for raw in chunks:
        carry += raw.decode("utf-8", errors="replace")
        while "\n\n" in carry:
            block, carry = carry.split("\n\n", 1)
            event_name = ""
            data_lines: list[str] = []
            for line in block.split("\n"):
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            if not data_lines:
                continue
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            kind = str(payload.get("type") or event_name)
            if kind == "message_start":
                message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
                msg_id = str(message.get("id") or msg_id)
                usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                prompt_tokens = _int_or_zero(usage.get("input_tokens"))
            elif kind == "content_block_delta":
                delta = payload.get("delta") if isinstance(payload.get("delta"), dict) else {}
                text = str(delta.get("text") or "")
                if text:
                    yield _openai_chunk(msg_id, content=text)
            elif kind == "message_delta":
                usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                if "output_tokens" in usage:
                    completion_tokens = _int_or_zero(usage.get("output_tokens"))
                delta = payload.get("delta") if isinstance(payload.get("delta"), dict) else {}
                stop = str(delta.get("stop_reason") or "")
                if stop:
                    yield _openai_chunk(
                        msg_id,
                        finish=_STOP_TO_FINISH.get(stop, "stop"),
                        usage={
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens,
                        },
                    )
            elif kind == "message_stop":
                yield b"data: [DONE]\n\n"
    if carry.strip():
        yield b"data: [DONE]\n\n"


def _openai_chunk(
    msg_id: str,
    *,
    content: str | None = None,
    finish: str | None = None,
    usage: dict[str, int] | None = None,
) -> bytes:
    choice: dict[str, Any] = {
        "index": 0,
        "delta": {"content": content} if content is not None else {},
        "finish_reason": finish,
    }
    body: dict[str, Any] = {
        "id": msg_id,
        "object": "chat.completion.chunk",
        "choices": [choice],
    }
    if usage is not None:
        body["usage"] = usage
    return f"data: {json.dumps(body, separators=(',', ':'))}\n\n".encode()


def _plain_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return "" if content is None else str(content)


def _anthropic_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "text":
                parts.append({"type": "text", "text": str(item.get("text") or "")})
            elif kind == "image_url":
                url_obj = item.get("image_url") if isinstance(item.get("image_url"), dict) else {}
                url = str(url_obj.get("url") or "")
                if url.startswith("data:"):
                    header, _, b64 = url.partition(",")
                    media = "image/png"
                    if "image/" in header:
                        media = header.split(";")[0].split(":", 1)[-1] or media
                    parts.append(
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media, "data": b64},
                        }
                    )
                elif url:
                    parts.append({"type": "image", "source": {"type": "url", "url": url}})
        return parts or ""
    return _plain_text(content)


def _concat_content(left: Any, right: Any) -> Any:
    if isinstance(left, str) and isinstance(right, str):
        return f"{left}\n{right}"
    left_parts = left if isinstance(left, list) else [{"type": "text", "text": str(left)}]
    right_parts = right if isinstance(right, list) else [{"type": "text", "text": str(right)}]
    return list(left_parts) + list(right_parts)


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
