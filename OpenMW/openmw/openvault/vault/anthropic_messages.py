"""Anthropic Messages API adapter for the OpenAI-shaped chat proxy.

Pure functions plus one async ``call``. Nothing imports this module yet: it is
not reachable from any route, so no request can be served through it and
outside models stay off (BANK-03). Wiring and the opt-in gate land after #154
through ``route_policy.json``.

Founder decision 2026-10-29: Claude is an opt-in escalation tier. The free pins
stay the default. This module only translates; it never decides whether to run.

Request mapping (OpenAI chat body -> ``POST /v1/messages``):

* ``system`` and ``developer`` messages are hoisted into the top-level ``system``.
* ``user`` and ``assistant`` turns map one to one. Consecutive same-role turns merge.
* ``max_tokens`` is required upstream. ``max_tokens``, then ``max_completion_tokens``,
  then ``DEFAULT_MAX_TOKENS``.
* ``stop`` becomes ``stop_sequences``.
* ``response_format`` json_object / json_schema: instruct-and-parse. The proxy
  has no schema handling of its own (it forwards ``response_format`` untouched
  to OpenAI-compatible hops), so this adapter adds a JSON-only instruction to
  ``system`` and strips code fences from the reply. No assistant prefill (400
  on the current Claude models) and no forced ``tool_choice`` (400 on Opus 5.5
  and Sonnet 5.5).
* ``temperature`` / ``top_p`` are dropped on purpose. Non-default sampling
  values are a 400 on the current Claude models, so forwarding the caller's
  value would turn a working request into a hard failure.
* ``thinking`` and ``effort`` are not sent. The models' own defaults apply.
* Tools, images, ``stream`` and ``n > 1`` raise ``UnsupportedRequestError``.
  ``call`` turns that into a typed 400. Image and tool parts are refused rather
  than silently dropped, for the reason ``proxy._is_multimodal`` documents.

Secrets: ``call`` takes a ``KeyHandle`` (a zero-argument callable), reveals the
key only while building the request header, and never logs, returns or
formats it. Error text from upstream goes through ``provider_error_text``,
which scrubs key-shaped substrings.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
import structlog

from openmw.openvault.vault.parks import provider_error_text

log = structlog.get_logger()

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Strongest first. Escalation default is the middle model: Sonnet 5.5 is
# the cheapest of the two top rungs that still reasons well. Ids verified
# against the claude-api reference (cached 2026-10-06); no date suffixes.
CHAT_MODELS: tuple[str, ...] = (
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
)
ESCALATION_DEFAULT_MODEL = "claude-sonnet-5-5"

# Required upstream. Generous enough that adaptive thinking (on by default on
# these models) cannot eat the whole budget before any text is written.
DEFAULT_MAX_TOKENS = 4096

KeyHandle = Callable[[], str]
"""Returns the key on demand. Pass a closure over the vault decrypt, not the key."""

_JSON_OBJECT_INSTRUCTION = (
    "Respond with a single valid JSON object and nothing else. "
    "No prose before or after it and no markdown code fences."
)
_JSON_SCHEMA_INSTRUCTION = (
    "Respond with a single valid JSON value that conforms to this JSON Schema, "
    "and nothing else. No prose before or after it and no markdown code fences.\n"
    "JSON Schema:\n{schema}"
)

_FINISH_REASONS: dict[str, str] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}

_FENCE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*\n(?P<body>.*?)\n?```\s*$", re.DOTALL)


class UnsupportedRequestError(ValueError):
    """The OpenAI body uses something this adapter will not translate."""


# --------------------------------------------------------------------------
# request


def _text_of(content: Any, *, role: str) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") in ("text", "input_text"):
                parts.append(str(part.get("text") or ""))
            else:
                kind = part.get("type") if isinstance(part, dict) else type(part).__name__
                raise UnsupportedRequestError(f"{role} message part type {kind!r} is not supported")
        return "".join(parts)
    raise UnsupportedRequestError(
        f"{role} message content must be a string or a list of text parts"
    )


def _json_instruction(response_format: Any) -> str | None:
    if not isinstance(response_format, dict):
        return None
    kind = response_format.get("type")
    if kind == "json_object":
        return _JSON_OBJECT_INSTRUCTION
    if kind == "json_schema":
        holder = response_format.get("json_schema")
        schema = holder.get("schema") if isinstance(holder, dict) else None
        if isinstance(schema, dict):
            return _JSON_SCHEMA_INSTRUCTION.format(
                schema=json.dumps(schema, sort_keys=True, separators=(",", ":"))
            )
        return _JSON_OBJECT_INSTRUCTION
    return None


def wants_json(body: dict[str, Any]) -> bool:
    """True when the caller asked for json_object or json_schema output."""
    return _json_instruction(body.get("response_format")) is not None


def to_anthropic_body(body: dict[str, Any], *, default_model: str | None = None) -> dict[str, Any]:
    """OpenAI chat body -> Anthropic Messages body. Raises ``UnsupportedRequestError``."""
    if body.get("stream"):
        raise UnsupportedRequestError("stream is not supported by the Anthropic adapter yet")
    n = body.get("n")
    if isinstance(n, int) and n > 1:
        raise UnsupportedRequestError("n > 1 is not supported by the Anthropic adapter")
    if body.get("tools") or body.get("functions") or body.get("tool_choice"):
        raise UnsupportedRequestError("tools are not supported by the Anthropic adapter yet")

    system_parts: list[str] = []
    turns: list[dict[str, str]] = []
    for msg in body.get("messages") or ():
        if not isinstance(msg, dict):
            raise UnsupportedRequestError("messages must be objects")
        role = msg.get("role")
        if role in ("tool", "function") or msg.get("tool_calls"):
            raise UnsupportedRequestError(
                "tool messages are not supported by the Anthropic adapter yet"
            )
        text = _text_of(msg.get("content"), role=str(role))
        if role in ("system", "developer"):
            if text.strip():
                system_parts.append(text)
        elif role in ("user", "assistant"):
            if not text.strip():
                continue
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"] += "\n\n" + text
            else:
                turns.append({"role": str(role), "content": text})
        else:
            raise UnsupportedRequestError(f"message role {role!r} is not supported")

    if not turns or turns[0]["role"] != "user":
        raise UnsupportedRequestError("the conversation must start with a non-empty user message")

    instruction = _json_instruction(body.get("response_format"))
    if instruction:
        system_parts.append(instruction)

    model = str(body.get("model") or "").strip()
    if not model or model.lower() in ("auto", "default"):
        model = default_model or ESCALATION_DEFAULT_MODEL

    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens") or DEFAULT_MAX_TOKENS
    out: dict[str, Any] = {
        "model": model,
        "max_tokens": int(max_tokens),
        "messages": turns,
    }
    if system_parts:
        out["system"] = "\n\n".join(system_parts)
    stop = body.get("stop")
    if isinstance(stop, str) and stop:
        out["stop_sequences"] = [stop]
    elif isinstance(stop, list) and stop:
        out["stop_sequences"] = [str(s) for s in stop if s]
    return out


# --------------------------------------------------------------------------
# response


def _strip_json(text: str) -> str:
    """Return the JSON text when it parses, with fences removed. Else unchanged."""
    candidate = text.strip()
    fenced = _FENCE.match(candidate)
    if fenced:
        candidate = fenced.group("body").strip()
    try:
        json.loads(candidate)
    except ValueError:
        return text
    return candidate


def from_anthropic_response(
    payload: dict[str, Any], *, json_mode: bool = False, model: str | None = None
) -> dict[str, Any]:
    """Anthropic Messages response -> OpenAI chat.completion body."""
    blocks = payload.get("content")
    text = "".join(
        str(b.get("text") or "")
        for b in (blocks if isinstance(blocks, list) else ())
        if isinstance(b, dict) and b.get("type") == "text"
    )
    if json_mode:
        text = _strip_json(text)
    raw_usage = payload.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    prompt = (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
    )
    completion = int(usage.get("output_tokens") or 0)
    stop_reason = str(payload.get("stop_reason") or "end_turn")
    return {
        "id": f"chatcmpl-{payload.get('id') or 'anthropic'}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(payload.get("model") or model or ""),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": _FINISH_REASONS.get(stop_reason, "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


# --------------------------------------------------------------------------
# errors


def map_error(status: int, text: str, headers: Any = None) -> tuple[int, dict[str, Any]]:
    """Upstream HTTP error -> (status, OpenAI-style error body) the proxy can classify.

    The proxy's classifier keys on the status: 401/403 auth, 429 rate limit,
    404 and some 400s model unavailable, and only 408/500/502/503/504 are
    retryable. Anthropic's 529 (overloaded) is not on that list, so it is
    presented as 503. Any other 5xx becomes 502. The message is scrubbed.
    """
    if status == 529:
        out_status, kind = 503, "overloaded_error"
    elif status >= 500:
        out_status = status if status in (500, 502, 503, 504) else 502
        kind = "api_error"
    elif status == 401:
        out_status, kind = 401, "authentication_error"
    elif status == 403:
        out_status, kind = 403, "permission_error"
    elif status == 429:
        out_status, kind = 429, "rate_limit_error"
    else:
        out_status, kind = status, "invalid_request_error"
    error: dict[str, Any] = {
        "message": provider_error_text(text, status=status),
        "type": kind,
        "provider": "anthropic",
    }
    retry_after = _retry_after(headers)
    if retry_after is not None:
        error["retry_after_s"] = retry_after
    return out_status, {"error": error}


def _retry_after(headers: Any) -> int | None:
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except AttributeError:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return int(value) if 0 <= value < 86_400 else None


def _unsupported_body(message: str) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": "openvault_anthropic_unsupported",
            "provider": "anthropic",
        }
    }


# --------------------------------------------------------------------------
# call


async def call(
    client: httpx.AsyncClient,
    key: KeyHandle,
    body: dict[str, Any],
    *,
    default_model: str | None = None,
    url: str = ANTHROPIC_MESSAGES_URL,
    timeout: float = 120.0,
) -> tuple[int, dict[str, Any]]:
    """Send one OpenAI-shaped chat body through Anthropic. Returns (status, body).

    HTTP failures come back as ``map_error`` output, not exceptions. Transport
    errors (timeout, connect) propagate as the ``httpx`` exceptions the proxy
    already catches around its other hops; they carry the URL, never headers.
    """
    try:
        payload = to_anthropic_body(body, default_model=default_model)
    except UnsupportedRequestError as exc:
        return 400, _unsupported_body(str(exc))

    headers = {
        "x-api-key": key(),
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
    request_id = resp.headers.get("request-id")
    if resp.status_code >= 400:
        status, err = map_error(resp.status_code, resp.text, resp.headers)
        log.warning(
            "openvault_anthropic_error",
            status=resp.status_code,
            mapped=status,
            model=payload["model"],
            request_id=request_id,
        )
        return status, err
    try:
        data = resp.json()
    except ValueError:
        return 502, {
            "error": {
                "message": "anthropic returned a non-JSON body",
                "type": "api_error",
                "provider": "anthropic",
            }
        }
    log.info("openvault_anthropic_ok", model=payload["model"], request_id=request_id)
    return 200, from_anthropic_response(data, json_mode=wants_json(body), model=payload["model"])
