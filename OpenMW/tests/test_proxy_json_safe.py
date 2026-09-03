"""A working hop must not become uvicorn text/plain Internal Server Error.

Live 2026-08-28: POST /v1/chat/completions model=auto (and groq gpt-oss / OpenRouter
ids) returned HTTP 500 content-type text/plain. Groq llama ids returned a typed
JSON 400. Cortex AGENT_TASK sends model=auto, so AirGPT chat looked dead.
"""
from __future__ import annotations

import json
import math

from openmw.openvault.vault.proxy import json_safe


def test_json_safe_round_trips_a_normal_chat_payload() -> None:
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "pong"}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }
    assert json_safe(payload) == payload


def test_json_safe_does_not_raise_on_nan_or_bytes() -> None:
    out = json_safe({"n": math.nan, "raw": b"\xff\xfe", "extra": {1, 2}})
    assert isinstance(out, dict)
    dumped = json.dumps(out)
    json.loads(dumped)


def test_json_safe_error_shape_is_typed() -> None:
    """If encode still cannot happen, the caller gets JSON they can show."""

    class Boom:
        def __str__(self) -> str:
            raise ValueError("nope")

    out = json_safe({"x": Boom()})
    assert out["error"]["type"] == "openvault_unserializable"
