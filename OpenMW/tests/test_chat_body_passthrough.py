"""ChatBody must not silently delete fields it does not itself interpret.

Pydantic drops undeclared fields by default. ChatBody declared only model/messages/
temperature/max_tokens/stream, so `tools` and `tool_choice` were deleted on the way
through the proxy. A caller could send 15 tool definitions, receive a 200, and be
told by the model that it had no tools - with nothing logged anywhere, because
nothing knew a field had been removed.

That is the worst shape of bug this repo keeps producing: a silent success that is
actually a loss. These tests pin the passthrough so it cannot regress.
"""

from __future__ import annotations

from openmw.openvault.app import ChatBody

_TOOL = {
    "type": "function",
    "function": {
        "name": "memory_search",
        "description": "Search memories",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}

_BASE = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}


def _dump(**extra) -> dict:
    return ChatBody(**{**_BASE, **extra}).model_dump(exclude_none=True)


def test_tools_survive_the_round_trip() -> None:
    out = _dump(tools=[_TOOL])
    assert out["tools"] == [_TOOL], "tool definitions were dropped by the request model"
    assert out["tools"][0]["function"]["name"] == "memory_search"


def test_tool_choice_survives_in_both_shapes() -> None:
    assert _dump(tool_choice="auto")["tool_choice"] == "auto"
    forced = {"type": "function", "function": {"name": "memory_search"}}
    assert _dump(tool_choice=forced)["tool_choice"] == forced


def test_undeclared_openai_fields_are_passed_through() -> None:
    """A proxy is transparent about what it does not interpret.

    top_p / stop / response_format / seed were being lost the same way as tools, and
    OpenAI keeps adding more - so the model allows extras rather than enumerating a
    list that will be stale next quarter.
    """
    out = _dump(
        top_p=0.95,
        stop=["\n\n"],
        seed=42,
        response_format={"type": "json_object"},
        parallel_tool_calls=False,
    )
    assert out["top_p"] == 0.95
    assert out["stop"] == ["\n\n"]
    assert out["seed"] == 42
    assert out["response_format"] == {"type": "json_object"}
    assert out["parallel_tool_calls"] is False


def test_declared_fields_still_work() -> None:
    out = _dump(temperature=0.3, max_tokens=256, stream=True)
    assert out["temperature"] == 0.3
    assert out["max_tokens"] == 256
    assert out["stream"] is True
    assert out["model"] == "auto"


def test_absent_optional_fields_are_not_invented() -> None:
    """exclude_none must not turn an unset field into a null the upstream rejects."""
    out = _dump()
    for k in ("tools", "tool_choice", "temperature", "max_tokens"):
        assert k not in out, f"{k} should be absent, not None, when unset"


def test_tools_survive_alongside_a_concrete_model() -> None:
    """The model-resolution fix rewrites `model`; it must not disturb `tools`."""
    out = _dump(model="openai/gpt-oss-120b", tools=[_TOOL], max_tokens=600)
    hop = {**out, "model": "llama-3.3-70b-versatile"}  # what proxy.py does per hop
    assert hop["tools"] == [_TOOL]
    assert hop["model"] == "llama-3.3-70b-versatile"
