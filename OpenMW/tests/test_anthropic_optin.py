"""Anthropic is an opt-in /v1 hop. No network and no real key.

Default route_policy.json does not name anthropic. A vault key whose provider
is anthropic is not called unless an override lists that hop. Caps and stamps
are the ROUTE-ROLE-BUDGET-01 rules: unpriced or over cap refuses before HTTP.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import structlog
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.route.breaker import reset_all_circuit_breakers
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.route_policy import (
    DEFAULT_POLICY_PATH,
    POLICY_ENV,
    ROUTE_ROLES,
    RoleHop,
    load_route_policy,
    reset_pending,
)
from openmw.openvault.vault.store import KeyRecord, KeyVault, ProviderKind

_SECRET = "sk-ant-api03-PLACEHOLDERPLACEHOLDER0123456789"
_GROQ = "https://api.groq.com/openai/v1"
_ANTHROPIC = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-5-5"
_IN_USD = 3.0
_OUT_USD = 15.0


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (POLICY_ENV, "OPENVAULT_LOCAL_BASE_URL", "OPENVAULT_MAX_OUTPUT_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    reset_all_circuit_breakers()
    reset_pending()
    yield
    reset_all_circuit_breakers()
    reset_pending()


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KeyVault:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    return KeyVault(db_path=tmp_path / "keys.db", seal=Seal(Fernet.generate_key()))


def _key(
    vault: KeyVault, provider: ProviderKind, *, label: str = "", base_url: str = ""
) -> KeyRecord:
    hosts = {"groq": _GROQ, "anthropic": "https://api.anthropic.com"}
    record = vault.create(
        label=label or provider,
        provider=provider,
        secret=_SECRET if provider == "anthropic" else f"placeholder-{provider}-not-a-key",
        role="primary",
        base_url=base_url or hosts[provider],
    )
    vault.set_precheck(record.id, status="ok", latency_ms=1.0, error=None)
    return record


def _policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, doc: dict[str, Any]) -> None:
    path = tmp_path / "route_policy.override.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv(POLICY_ENV, str(path))


def _priced(**caps: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "roles": {"reason": [{"provider": "anthropic", "model": _MODEL}]},
        "models": {
            f"anthropic/{_MODEL}": {
                "free": False,
                "usd_per_1m_in": _IN_USD,
                "usd_per_1m_out": _OUT_USD,
            }
        },
    }
    if caps:
        doc["caps"] = caps
    return doc


def _client(vault: KeyVault) -> TestClient:
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def _post(client: TestClient, handler: Any, body: dict[str, Any]) -> Any:
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def factory(*_args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real(**kwargs)

    payload = {"model": "auto", "messages": [{"role": "user", "content": "hello"}], **body}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", factory):
        return client.post("/v1/chat/completions", json=payload)


def _anthropic_ok() -> dict[str, Any]:
    return {
        "id": "msg_01ABC",
        "type": "message",
        "role": "assistant",
        "model": _MODEL,
        "content": [{"type": "text", "text": "hello from claude"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1000, "output_tokens": 500},
    }


def _groq_ok() -> dict[str, Any]:
    return {
        "id": "chatcmpl-groq",
        "object": "chat.completion",
        "model": "openai/gpt-oss-20b",
        "choices": [
            {"message": {"role": "assistant", "content": "from groq"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
    }


def _err(status: int, *, retry_after: str | None = None) -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after else {}
    return httpx.Response(
        status,
        json={"type": "error", "error": {"type": "overloaded_error", "message": f"nope {status}"}},
        headers=headers,
    )


def test_default_policy_has_no_anthropic_hop() -> None:
    policy = load_route_policy()
    assert policy.sources == (str(DEFAULT_POLICY_PATH),)
    assert "claude" not in DEFAULT_POLICY_PATH.read_text(encoding="utf-8")
    for role in ROUTE_ROLES:
        assert all(hop.provider != "anthropic" for hop in policy.hops_for(role)), role


_CLAUDE_EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "route_policy.claude.json"
_SONNET = RoleHop("anthropic", "claude-sonnet-5-5")
_OPUS = RoleHop("anthropic", "claude-opus-5-5")
_FREE_PROVIDERS = frozenset({"groq", "google", "openrouter", "nvidia"})
# Founder 2026-11-27: answer/sql serving is Sonnet; hard work, grain, ontology are Opus.
_CLAUDE_HOPS = {
    "sql": (
        _SONNET,
        RoleHop("groq", "qwen/qwen3.8-27b"),
        RoleHop("openrouter", "qwen/qwen3.8-27b:free"),
    ),
    "tool": (
        _SONNET,
        RoleHop("groq", "openai/gpt-oss-20b"),
        RoleHop("google", "gemini-3.1-flash-lite"),
        RoleHop("nvidia", "openai/gpt-oss-20b"),
    ),
    "summarize": (
        _SONNET,
        RoleHop("google", "gemini-3.1-flash-lite"),
        RoleHop("groq", "openai/gpt-oss-20b"),
        RoleHop("openrouter", "google/gemma-4-31b-it:free"),
    ),
    "reason": (
        _OPUS,
        RoleHop("groq", "openai/gpt-oss-120b"),
        RoleHop("nvidia", "nvidia/nemotron-3-super-120b-a12b"),
        RoleHop("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ),
    "grain": (
        _OPUS,
        RoleHop("groq", "openai/gpt-oss-20b"),
        RoleHop("google", "gemini-3.1-flash-lite"),
        RoleHop("openrouter", "google/gemma-4-31b-it:free"),
    ),
    "ontology": (
        _OPUS,
        RoleHop("groq", "openai/gpt-oss-120b"),
        RoleHop("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        RoleHop("google", "gemini-3.5-flash"),
    ),
}


def test_claude_example_is_not_the_default_and_has_one_daily_knob() -> None:
    assert _CLAUDE_EXAMPLE.is_file()
    assert _CLAUDE_EXAMPLE.resolve() != DEFAULT_POLICY_PATH.resolve()
    raw = json.loads(_CLAUDE_EXAMPLE.read_text(encoding="utf-8"))
    assert raw["caps"] == {"callers": {"dms": {"daily_usd": 20}}}
    assert raw["models"]["anthropic/claude-sonnet-5-5"] == {
        "free": False,
        "usd_per_1m_in": 2,
        "usd_per_1m_out": 10,
    }
    assert raw["models"]["anthropic/claude-opus-5-5"] == {
        "free": False,
        "usd_per_1m_in": 4,
        "usd_per_1m_out": 20,
    }


def test_claude_example_orders_hops_and_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(POLICY_ENV, str(_CLAUDE_EXAMPLE))
    policy = load_route_policy()
    assert policy.sources[0] == str(DEFAULT_POLICY_PATH)
    assert policy.sources[1] == str(_CLAUDE_EXAMPLE)
    assert set(policy.caller_caps) == {"dms"}
    assert policy.caller_caps["dms"].daily_usd == 20
    assert policy.key_caps == {}
    sonnet = policy.price_for("anthropic", "claude-sonnet-5-5")
    opus = policy.price_for("anthropic", "claude-opus-5-5")
    assert sonnet.cost(1_000_000, 0) == 2
    assert sonnet.cost(0, 1_000_000) == 10
    assert opus.cost(1_000_000, 0) == 4
    assert opus.cost(0, 1_000_000) == 20
    for role, hops in _CLAUDE_HOPS.items():
        assert policy.hops_for(role) == hops, role
        assert all(hop.provider in _FREE_PROVIDERS for hop in hops[1:])
        for hop in hops[1:]:
            assert policy.price_for(hop.provider, hop.model).free is True


def test_gate_off_does_not_call_anthropic_and_keeps_the_historical_skip(vault: KeyVault) -> None:
    _key(vault, "anthropic")
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=_groq_ok())

    resp = _post(_client(vault), handler, {})
    assert resp.status_code == 502
    details = " ".join(resp.json()["error"].get("details") or [])
    assert "anthropic chat not via /v1 proxy yet" in details
    assert urls == []
    assert _SECRET not in resp.text


def test_gate_off_success_matches_a_pool_without_the_anthropic_key(vault: KeyVault) -> None:
    """A successful default role reply does not change when an unused key is present."""
    _key(vault, "groq")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "anthropic.com" not in str(request.url)
        return httpx.Response(200, json=_groq_ok())

    first = _post(_client(vault), handler, {"role": "sql"}).json()
    _key(vault, "anthropic", label="claude-credit")
    second = _post(_client(vault), handler, {"role": "sql"}).json()
    assert first == second
    assert first["provider"] == "groq"
    assert "anthropic" not in json.dumps(first)


def test_label_text_is_not_enough_without_provider_anthropic(vault: KeyVault) -> None:
    _key(vault, "groq", label="anthropic")
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=_groq_ok())

    resp = _post(_client(vault), handler, {"role": "sql"})
    assert resp.status_code == 200
    assert urls
    assert all("anthropic.com" not in url for url in urls)
    assert resp.json()["provider"] == "groq"


def test_policy_hop_without_a_vault_key_does_not_call_anthropic(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "groq")
    _policy(
        tmp_path,
        monkeypatch,
        _priced(callers={"cortex": {"daily_usd": 10}}),
    )
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=_groq_ok())

    resp = _post(_client(vault), handler, {"role": "reason", "service_id": "cortex"})
    assert resp.status_code >= 400
    assert urls == []
    assert resp.json()["provider"] is None


def test_gate_on_translates_and_stamps_cost(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "anthropic")
    _policy(tmp_path, monkeypatch, _priced(callers={"cortex": {"daily_usd": 10}}))
    seen: dict[str, Any] = {}
    captured: list[dict[str, Any]] = []

    def processor(_logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
        captured.append(dict(event))
        return event

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json=_anthropic_ok())

    saved = structlog.get_config()
    structlog.configure(processors=[processor, *saved["processors"]])
    try:
        resp = _post(
            _client(vault),
            handler,
            {
                "role": "reason",
                "service_id": "cortex",
                "messages": [
                    {"role": "system", "content": "be terse"},
                    {"role": "user", "content": "hello"},
                ],
            },
        )
    finally:
        structlog.configure(**saved)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert seen["url"] == _ANTHROPIC
    assert seen["headers"]["x-api-key"] == _SECRET
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["json"]["model"] == _MODEL
    assert seen["json"]["system"] == "be terse"
    assert seen["json"]["messages"][0]["role"] == "user"
    assert "temperature" not in seen["json"]
    assert body["choices"][0]["message"]["content"] == "hello from claude"
    assert body["provider"] == "anthropic"
    assert body["model"] == _MODEL
    assert body["served_provider"] == "anthropic"
    assert body["tokens_in"] == 1000
    assert body["tokens_out"] == 500
    assert body["est_cost_usd"] == round((1000 * _IN_USD + 500 * _OUT_USD) / 1_000_000, 8)
    blob = json.dumps({"body": body, "wire": seen["json"], "logs": captured}, default=str)
    assert _SECRET not in blob
    assert "PLACEHOLDERPLACEHOLDER" not in blob


def test_cap_refuses_before_http(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "anthropic")
    _policy(tmp_path, monkeypatch, _priced(callers={"cortex": {"daily_usd": 0.0000001}}))
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=_anthropic_ok())

    resp = _post(_client(vault), handler, {"role": "reason", "service_id": "cortex"})
    assert resp.status_code == 402
    err = resp.json()["error"]
    assert err["reason"] == "budget_exceeded"
    assert err["cap"] == "caller.daily_usd"
    assert urls == []
    assert resp.json()["est_cost_usd"] == 0.0


def test_unpriced_anthropic_hop_is_refused(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "anthropic")
    _policy(
        tmp_path,
        monkeypatch,
        {
            "roles": {"reason": [{"provider": "anthropic", "model": _MODEL}]},
            "caps": {"callers": {"cortex": {"daily_usd": 10}}},
        },
    )
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=_anthropic_ok())

    resp = _post(_client(vault), handler, {"role": "reason", "service_id": "cortex"})
    assert resp.status_code == 402
    assert resp.json()["error"]["cap_detail"]["requested"] is None
    assert "no price" in resp.json()["error"]["message"]
    assert resp.json()["est_cost_usd"] == "cost_unknown"
    assert urls == []


@pytest.mark.parametrize("status", [401, 429, 529])
def test_auth_rate_limit_and_overload_walk_to_the_next_hop(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    _key(vault, "anthropic")
    _key(vault, "groq")
    _policy(
        tmp_path,
        monkeypatch,
        {
            "roles": {
                "reason": [
                    {"provider": "anthropic", "model": _MODEL},
                    {"provider": "groq", "model": "openai/gpt-oss-20b"},
                ]
            },
            "models": {
                f"anthropic/{_MODEL}": {
                    "free": False,
                    "usd_per_1m_in": _IN_USD,
                    "usd_per_1m_out": _OUT_USD,
                }
            },
            "caps": {"callers": {"cortex": {"daily_usd": 10}}},
        },
    )
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if "anthropic.com" in str(request.url):
            retry = "17" if status == 429 else None
            return _err(status, retry_after=retry)
        return httpx.Response(200, json=_groq_ok())

    resp = _post(_client(vault), handler, {"role": "reason", "service_id": "cortex"})
    assert resp.status_code == 200, resp.text
    assert urls[0] == _ANTHROPIC
    assert urls[1].startswith(_GROQ)
    assert resp.json()["provider"] == "groq"
    assert _SECRET not in resp.text


def test_stream_against_only_anthropic_is_a_typed_400(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "anthropic")
    _policy(tmp_path, monkeypatch, _priced(callers={"cortex": {"daily_usd": 10}}))
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=_anthropic_ok())

    resp = _post(
        _client(vault),
        handler,
        {"role": "reason", "service_id": "cortex", "stream": True},
    )
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["type"] == "openvault_anthropic_unsupported"
    assert err["provider"] == "anthropic"
    assert "stream is not supported" in err["message"]
    assert urls == []


def test_stream_falls_through_to_the_next_hop(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "anthropic")
    _key(vault, "groq")
    _policy(
        tmp_path,
        monkeypatch,
        {
            "roles": {
                "reason": [
                    {"provider": "anthropic", "model": _MODEL},
                    {"provider": "groq", "model": "openai/gpt-oss-20b"},
                ]
            },
            "models": {
                f"anthropic/{_MODEL}": {
                    "free": False,
                    "usd_per_1m_in": _IN_USD,
                    "usd_per_1m_out": _OUT_USD,
                }
            },
            "caps": {"callers": {"cortex": {"daily_usd": 10}}},
        },
    )
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(
            200,
            content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    resp = _post(
        _client(vault),
        handler,
        {"role": "reason", "service_id": "cortex", "stream": True},
    )
    assert resp.status_code == 200, resp.text
    assert urls
    assert all("anthropic.com" not in url for url in urls)
    assert urls[0].startswith(_GROQ)
    assert "hi" in resp.text
