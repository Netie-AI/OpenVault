"""Anthropic Messages adapter: mapping, errors, and key hygiene. No network."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import traceback
from pathlib import Path
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from openmw.openvault.route.attempt import classify_attempt
from openmw.openvault.vault import anthropic_messages as am

# Shaped like a real key so the scrubber and the leak checks have something to find.
FAKE_KEY = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKE0123456789"


def _ok_payload(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "msg_01ABC",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": "hello there"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 11, "output_tokens": 5},
    }
    base.update(over)
    return base


def _run(handler: Any, body: dict[str, Any], **kw: Any) -> tuple[int, dict[str, Any]]:
    async def go() -> tuple[int, dict[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await am.call(client, lambda: FAKE_KEY, body, **kw)

    return asyncio.run(go())


def _body(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "claude-sonnet-5-5",
        "messages": [{"role": "user", "content": "hi"}],
    }
    base.update(over)
    return base


# -- off by default: the wiring is in test_anthropic_optin_wire.py -----------------


def test_only_the_proxy_and_providers_import_the_adapter() -> None:
    """Dispatch lives in proxy.py and the catalog in providers.py. Nothing else."""
    root = Path(__file__).resolve().parents[1] / "openmw"
    me = Path(am.__file__).resolve()
    importers: set[str] = set()
    for path in root.rglob("*.py"):
        if path.resolve() == me:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any("anthropic_messages" in n for n in names):
                importers.add(path.name)
    assert importers == {"proxy.py", "providers.py"}


def test_anthropic_is_catalogued_but_not_spendable_or_compat() -> None:
    from openmw.openvault.vault.providers import get_provider, spendable_for_freeroute
    from openmw.openvault.vault.proxy import _compat_skip

    spec = get_provider("anthropic")
    assert spec is not None
    assert spec.openai_compatible is False
    assert spec.chat_models == am.CHAT_MODELS
    assert spec.to_dict()["spendable"] is False
    assert "anthropic" not in {row["id"] for row in spendable_for_freeroute()}
    # Without a role step the pool walk still skips it.
    assert _compat_skip("A", "anthropic") is not None
    assert _compat_skip("A", "anthropic", role_step=True) is None


# -- request mapping ---------------------------------------------------------------


def test_system_is_hoisted_and_turns_map() -> None:
    out = am.to_anthropic_body(
        _body(
            messages=[
                {"role": "system", "content": "be terse"},
                {"role": "developer", "content": "no emoji"},
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": [{"type": "text", "text": "q2"}]},
            ]
        )
    )
    assert out["system"] == "be terse\n\nno emoji"
    assert out["messages"] == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]
    assert out["model"] == "claude-sonnet-5-5"


def test_consecutive_same_role_turns_merge() -> None:
    out = am.to_anthropic_body(
        _body(messages=[{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
    )
    assert out["messages"] == [{"role": "user", "content": "a\n\nb"}]


def test_max_tokens_default_and_aliases() -> None:
    assert am.to_anthropic_body(_body())["max_tokens"] == am.DEFAULT_MAX_TOKENS
    assert am.to_anthropic_body(_body(max_tokens=77))["max_tokens"] == 77
    assert am.to_anthropic_body(_body(max_completion_tokens=88))["max_tokens"] == 88


def test_model_auto_and_missing_use_the_escalation_default() -> None:
    assert am.to_anthropic_body(_body(model="auto"))["model"] == am.ESCALATION_DEFAULT_MODEL
    assert am.to_anthropic_body({"messages": _body()["messages"]})["model"] == "claude-sonnet-5-5"
    assert am.to_anthropic_body(_body(model="claude-opus-5-5"))["model"] == "claude-opus-5-5"


def test_model_catalog_is_strongest_first_with_sonnet_default() -> None:
    assert am.CHAT_MODELS == ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
    assert am.ESCALATION_DEFAULT_MODEL == "claude-sonnet-5-5"


def test_stop_maps_to_stop_sequences() -> None:
    assert am.to_anthropic_body(_body(stop="END"))["stop_sequences"] == ["END"]
    assert am.to_anthropic_body(_body(stop=["a", "b"]))["stop_sequences"] == ["a", "b"]


def test_sampling_params_are_dropped_not_forwarded() -> None:
    out = am.to_anthropic_body(_body(temperature=0.2, top_p=0.9, seed=1, user="u"))
    assert set(out) == {"model", "max_tokens", "messages"}


def test_json_object_adds_instruction_and_no_prefill() -> None:
    out = am.to_anthropic_body(_body(response_format={"type": "json_object"}))
    assert "valid JSON object" in out["system"]
    assert out["messages"][-1]["role"] == "user"


def test_json_schema_embeds_the_schema() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    out = am.to_anthropic_body(
        _body(
            response_format={"type": "json_schema", "json_schema": {"name": "x", "schema": schema}}
        )
    )
    assert '"properties":{"n":{"type":"integer"}}' in out["system"]


@pytest.mark.parametrize(
    "over",
    [
        {"stream": True},
        {"n": 2},
        {"tools": [{"type": "function", "function": {"name": "f"}}]},
        {"messages": [{"role": "tool", "content": "x"}]},
        {"messages": [{"role": "assistant", "content": "x"}]},
        {
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "u"}}]}
            ]
        },
    ],
)
def test_unsupported_requests_raise(over: dict[str, Any]) -> None:
    with pytest.raises(am.UnsupportedRequestError):
        am.to_anthropic_body(_body(**over))


# -- response mapping --------------------------------------------------------------


def test_response_maps_text_usage_and_finish_reason() -> None:
    out = am.from_anthropic_response(
        _ok_payload(
            content=[
                {"type": "thinking", "thinking": ""},
                {"type": "text", "text": "a"},
                {"type": "text", "text": "b"},
            ],
            usage={
                "input_tokens": 10,
                "cache_read_input_tokens": 4,
                "cache_creation_input_tokens": 1,
                "output_tokens": 6,
            },
        )
    )
    assert out["object"] == "chat.completion"
    assert out["choices"][0]["message"] == {"role": "assistant", "content": "ab"}
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"] == {"prompt_tokens": 15, "completion_tokens": 6, "total_tokens": 21}
    assert out["model"] == "claude-sonnet-5-5"


@pytest.mark.parametrize(
    ("stop_reason", "finish"),
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("tool_use", "tool_calls"),
        ("refusal", "content_filter"),
        ("something_new", "stop"),
    ],
)
def test_stop_reason_maps_to_finish_reason(stop_reason: str, finish: str) -> None:
    out = am.from_anthropic_response(_ok_payload(stop_reason=stop_reason))
    assert out["choices"][0]["finish_reason"] == finish


def test_json_mode_strips_fences_only_when_it_parses() -> None:
    fenced = _ok_payload(content=[{"type": "text", "text": '```json\n{"a": 1}\n```'}])
    assert (
        am.from_anthropic_response(fenced, json_mode=True)["choices"][0]["message"]["content"]
        == '{"a": 1}'
    )
    # Not JSON: left exactly as the model wrote it, never "repaired".
    prose = _ok_payload(content=[{"type": "text", "text": "I cannot do that."}])
    assert (
        am.from_anthropic_response(prose, json_mode=True)["choices"][0]["message"]["content"]
        == "I cannot do that."
    )
    # Fences are left alone when the caller did not ask for JSON.
    assert am.from_anthropic_response(fenced)["choices"][0]["message"]["content"].startswith("```")


# -- call: request on the wire -----------------------------------------------------


def test_call_serves_with_the_documented_headers_and_maps_the_reply() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_payload(), headers={"request-id": "req_1"})

    status, out = _run(
        handler,
        _body(messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]),
    )
    assert status == 200
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == FAKE_KEY
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["json"]["system"] == "s"
    assert seen["json"]["max_tokens"] == am.DEFAULT_MAX_TOKENS
    assert out["choices"][0]["message"]["content"] == "hello there"
    assert FAKE_KEY not in json.dumps(out)


def test_call_json_schema_round_trip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_ok_payload(content=[{"type": "text", "text": '```json\n{"n": 3}\n```'}])
        )

    status, out = _run(handler, _body(response_format={"type": "json_object"}))
    assert status == 200
    assert json.loads(out["choices"][0]["message"]["content"]) == {"n": 3}


def test_call_unsupported_is_a_typed_400_and_sends_nothing() -> None:
    hits: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(1)
        return httpx.Response(200, json=_ok_payload())

    status, out = _run(handler, _body(stream=True))
    assert status == 400
    assert out["error"]["type"] == "openvault_anthropic_unsupported"
    assert hits == []


def test_call_non_json_success_body_is_a_502() -> None:
    status, out = _run(lambda r: httpx.Response(200, text="<html>"), _body())
    assert status == 502
    assert out["error"]["type"] == "api_error"


# -- error mapping -----------------------------------------------------------------


def _err(
    status: int, message: str = "boom", headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": "error", "error": {"type": "x", "message": message}},
        headers=headers or {},
    )


@pytest.mark.parametrize(
    ("upstream", "mapped", "kind"),
    [
        (401, 401, "authentication_error"),
        (403, 403, "permission_error"),
        (429, 429, "rate_limit_error"),
        (529, 503, "overloaded_error"),
        (500, 500, "api_error"),
        (503, 503, "api_error"),
        (520, 502, "api_error"),
        (400, 400, "invalid_request_error"),
        (404, 404, "invalid_request_error"),
    ],
)
def test_http_errors_map_to_proxy_statuses(upstream: int, mapped: int, kind: str) -> None:
    status, out = _run(lambda r: _err(upstream), _body())
    assert status == mapped
    assert out["error"]["type"] == kind
    assert out["error"]["provider"] == "anthropic"


def test_429_carries_retry_after_and_proxy_classifies_the_mapped_statuses() -> None:
    status, out = _run(lambda r: _err(429, headers={"retry-after": "17"}), _body())
    assert out["error"]["retry_after_s"] == 17
    assert classify_attempt(status, out["error"]["message"]).attempt_class == "rate_limit"
    status, out = _run(lambda r: _err(529), _body())
    assert classify_attempt(status, out["error"]["message"]).trip_provider_breaker is True
    status, out = _run(lambda r: _err(401), _body())
    assert classify_attempt(status, out["error"]["message"]).attempt_class in (
        "auth_fail",
        "auth_stale",
    )


# -- key hygiene -------------------------------------------------------------------


def test_key_never_reaches_logs_results_or_exceptions(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    # capture_logs restores the previous processors on exit; reset_defaults() here
    # left structlog printing debug lines to stdout and broke test_cli's --json parse.
    with capture_logs() as captured:
        # Upstream echoes the key back in its error text (some providers do).
        status, out = _run(lambda r: _err(401, f"invalid x-api-key {FAKE_KEY}"), _body())
        assert status == 401
        assert FAKE_KEY not in json.dumps(out)
        assert "FAKEFAKE" not in json.dumps(out)

        _run(lambda r: httpx.Response(200, json=_ok_payload()), _body())

        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with pytest.raises(httpx.ConnectError) as info:
            _run(boom, _body())
        rendered = (
            "".join(traceback.format_exception(info.value)) + repr(info.value) + str(info.value)
        )
        assert FAKE_KEY not in rendered

    assert captured, "adapter logged nothing; the leak check would be vacuous"
    assert FAKE_KEY not in json.dumps(captured, default=str)
    assert FAKE_KEY not in caplog.text


def test_key_is_only_revealed_inside_call() -> None:
    reveals: list[int] = []

    def handle() -> str:
        reveals.append(1)
        return FAKE_KEY

    async def go() -> None:
        handler = lambda r: httpx.Response(200, json=_ok_payload())  # noqa: E731
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await am.call(client, handle, _body(stream=True))
            assert reveals == []  # refused before the key is touched
            await am.call(client, handle, _body())
            assert reveals == [1]

    asyncio.run(go())
