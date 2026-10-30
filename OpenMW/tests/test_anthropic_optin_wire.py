"""F110: Claude as an opt-in escalation tier, wired through the role router.

No network and no real keys: upstream httpx is mocked and every secret below is a
placeholder. The prices in these tests are test numbers, not Anthropic's.

What must hold:

* The shipped policy serves no Anthropic hop for any role (BANK-03).
* Anthropic serves only when an operator route policy names it in a role, prices
  it, and caps it, AND a healthy pooled vault key for provider anthropic exists.
* A pool walk, a strict pin and a local-only request cannot reach it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.route.breaker import reset_all_circuit_breakers
from openmw.openvault.vault import anthropic_messages as am
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.providers import get_provider, spendable_for_freeroute
from openmw.openvault.vault.route_policy import (
    ALL_ROLES,
    DEFAULT_POLICY_PATH,
    OPT_IN_ROLES,
    POLICY_ENV,
    RoutePolicyError,
    load_route_policy,
    reset_pending,
)
from openmw.openvault.vault.store import KeyRecord, KeyVault

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_HOSTS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "sambanova": "https://api.sambanova.ai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "sea_lion": "https://api.sea-lion.ai/v1",
    "anthropic": "https://api.anthropic.com",
}
_FREE = ("groq", "openrouter", "sambanova", "google", "nvidia", "sea_lion")
_STAMPS = ("model", "provider", "tokens_in", "tokens_out", "est_cost_usd")
_SONNET = "claude-sonnet-5-5"
# Test numbers only (USD per 1M tokens).
_IN, _OUT = 3.0, 15.0


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


def _key(vault: KeyVault, provider: str, priority: int = 0) -> KeyRecord:
    record = vault.create(
        label=f"{provider}-{priority}",
        provider=provider,
        secret=f"placeholder-{provider}-{priority}-not-a-key",
        role="primary",
        priority=priority,
        base_url=_HOSTS[provider],
    )
    vault.set_precheck(record.id, status="ok", latency_ms=5.0, error=None)
    return record


def _secret(provider: str, priority: int = 0) -> str:
    return f"placeholder-{provider}-{priority}-not-a-key"


def _policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, doc: dict[str, Any]) -> Path:
    path = tmp_path / "route_policy.override.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv(POLICY_ENV, str(path))
    return path


def _optin_policy(key_id: str | None, **caps: Any) -> dict[str, Any]:
    """An operator file: escalate -> Sonnet, priced with test numbers, key-capped."""
    doc: dict[str, Any] = {
        "roles": {"escalate": [{"provider": "anthropic", "model": _SONNET}]},
        "models": {f"anthropic/{_SONNET}": {"usd_per_1m_in": _IN, "usd_per_1m_out": _OUT}},
        "caps": {},
    }
    if key_id is not None:
        doc["caps"]["keys"] = {key_id: {"daily_usd": caps.pop("daily_usd", 5.0)}}
    doc["caps"].update(caps)
    return doc


def _openai_ok(model: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = ""
    resp.headers = {}
    resp.json = MagicMock(
        return_value={
            "id": "chatcmpl-mock",
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
        }
    )
    return resp


def _claude_ok(
    text: str = "claude says hi", tokens_in: int = 40, tokens_out: int = 10
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = ""
    resp.headers = {"request-id": "req_test"}
    resp.json = MagicMock(
        return_value={
            "id": "msg_test",
            "type": "message",
            "model": _SONNET,
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
        }
    )
    return resp


def _fail(status: int, text: str = "upstream unavailable") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.headers = {}
    resp.json = MagicMock(return_value={"error": "unavailable"})
    return resp


class _Recorder:
    def __init__(self, *responses: MagicMock) -> None:
        self.urls: list[str] = []
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self._responses = list(responses)
        mock = MagicMock()
        mock.post = AsyncMock(side_effect=self._post)
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.aclose = AsyncMock(return_value=None)
        self.mock = mock

    async def _post(self, url: str, **kwargs: Any) -> MagicMock:
        self.urls.append(url)
        self.bodies.append(dict(kwargs.get("json") or {}))
        self.headers.append(dict(kwargs.get("headers") or {}))
        if not self._responses:
            raise AssertionError(f"unexpected upstream call {url}")
        return self._responses.pop(0)

    @property
    def anthropic_calls(self) -> int:
        return sum(1 for url in self.urls if "anthropic.com" in url)


def _client(vault: KeyVault) -> TestClient:
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def _post(client: TestClient, rec: _Recorder, body: dict[str, Any]) -> Any:
    payload = {"model": "auto", "messages": [{"role": "user", "content": "hello"}], **body}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=rec.mock):
        return client.post("/v1/chat/completions", json=payload)


# --- BANK-03: off by default -------------------------------------------------


def test_shipped_policy_names_no_anthropic_hop_for_any_role() -> None:
    policy = load_route_policy()
    for role in ALL_ROLES:
        assert all(h.provider != "anthropic" for h in policy.hops_for(role)), role
    for role in OPT_IN_ROLES:
        assert policy.hops_for(role) == ()
    shipped = json.loads(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    assert "anthropic" not in json.dumps(shipped).lower()
    assert "claude" not in json.dumps(shipped).lower()
    # And nothing in the shipped table prices or frees it.
    assert policy.price_for("anthropic", _SONNET).free is False
    assert policy.price_for("anthropic", _SONNET).usd_per_1m_in is None


@pytest.mark.parametrize("role", [*ALL_ROLES, None])
def test_default_policy_with_an_anthropic_key_never_dispatches_anthropic(
    vault: KeyVault, role: str | None
) -> None:
    # Anthropic sorts first (priority 0), so a leak would be the first hop tried.
    _key(vault, "anthropic", priority=0)
    for i, provider in enumerate(_FREE, start=1):
        _key(vault, provider, priority=i)
    rec = _Recorder(*[_openai_ok("m") for _ in range(8)])
    body: dict[str, Any] = {"service_id": "cortex"}
    if role is not None:
        body["role"] = role
    resp = _post(_client(vault), rec, body)

    assert rec.anthropic_calls == 0, (role, rec.urls)
    if role == "escalate":
        # Accepted, but the shipped policy lists no hops: a named refusal, no call.
        assert resp.status_code == 502
        assert rec.urls == []
        assert any("opt-in tier is off" in d for d in resp.json()["error"]["details"])
    else:
        assert resp.status_code == 200, resp.text
        assert resp.json()["provider"] != "anthropic"


def test_pool_walk_with_only_an_anthropic_key_skips_it(vault: KeyVault) -> None:
    _key(vault, "anthropic")
    rec = _Recorder()
    resp = _post(_client(vault), rec, {})
    assert resp.status_code == 502
    assert rec.urls == []
    assert any("opt-in" in d for d in resp.json()["error"]["details"])


def test_strict_pin_on_a_claude_id_cannot_reach_anthropic(vault: KeyVault) -> None:
    _key(vault, "anthropic")
    rec = _Recorder()
    resp = _post(_client(vault), rec, {"model": _SONNET, "strict": True})
    assert resp.status_code >= 400
    assert rec.urls == []


def test_catalog_lists_claude_without_putting_it_in_any_pool() -> None:
    spec = get_provider("anthropic")
    assert spec is not None
    assert spec.chat_models == am.CHAT_MODELS
    assert spec.openai_compatible is False
    assert spec.to_dict()["spendable"] is False
    assert "anthropic" not in {row["id"] for row in spendable_for_freeroute()}


def test_a_policy_cannot_mark_anthropic_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _optin_policy(None)
    doc["models"] = {"anthropic/*": {"free": True}}
    _policy(tmp_path, monkeypatch, doc)
    with pytest.raises(RoutePolicyError, match="cannot be marked free"):
        load_route_policy()


# --- opt-in: policy + price + cap + key --------------------------------------


def test_opt_in_dispatches_and_returns_an_openai_body_with_stamps(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = _key(vault, "anthropic")
    for i, provider in enumerate(_FREE, start=1):
        _key(vault, provider, priority=i)
    _policy(tmp_path, monkeypatch, _optin_policy(claude.id))
    rec = _Recorder(_claude_ok("claude says hi", tokens_in=40, tokens_out=10))

    resp = _post(_client(vault), rec, {"role": "escalate", "service_id": "cortex"})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert rec.urls == [_ANTHROPIC_URL]
    assert rec.headers[0]["x-api-key"] == _secret("anthropic")
    assert "authorization" not in {k.lower() for k in rec.headers[0]}
    assert data["choices"][0]["message"]["content"] == "claude says hi"
    assert data["choices"][0]["finish_reason"] == "stop"
    for name in _STAMPS:
        assert name in data, name
    assert data["provider"] == "anthropic"
    assert data["model"] == _SONNET
    assert data["tokens_in"] == 40
    assert data["tokens_out"] == 10
    assert data["est_cost_usd"] == round((40 * _IN + 10 * _OUT) / 1_000_000.0, 8)
    assert data["served_provider"] == "anthropic"
    assert data["served_model"] == _SONNET
    # The key is not echoed anywhere in the response.
    assert _secret("anthropic") not in resp.text


def test_opt_in_without_a_vault_key_is_skipped_with_a_named_reason(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "groq")
    _policy(tmp_path, monkeypatch, _optin_policy("some-key-id"))
    rec = _Recorder()

    resp = _post(_client(vault), rec, {"role": "escalate", "service_id": "cortex"})

    assert resp.status_code == 502
    assert rec.urls == []
    details = resp.json()["error"]["details"]
    assert any(
        f"anthropic/{_SONNET}: no healthy vault key for provider anthropic" in d for d in details
    ), details


def test_opt_in_without_a_cap_is_refused_as_paid_default(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "anthropic")
    _policy(tmp_path, monkeypatch, _optin_policy(None))
    rec = _Recorder()

    resp = _post(_client(vault), rec, {"role": "escalate", "service_id": "cortex"})

    assert resp.status_code == 402
    assert resp.json()["error"]["cap"] == "paid_default"
    assert rec.urls == []


def test_opt_in_without_a_price_is_refused(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = _key(vault, "anthropic")
    doc = _optin_policy(claude.id)
    doc["models"] = {}
    _policy(tmp_path, monkeypatch, doc)
    rec = _Recorder()

    resp = _post(_client(vault), rec, {"role": "escalate", "service_id": "cortex"})

    assert resp.status_code == 402
    assert rec.urls == []
    assert "no price" in resp.json()["error"]["message"]


def test_a_free_hop_before_claude_serves_first(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = _key(vault, "anthropic")
    _key(vault, "groq", priority=1)
    doc = _optin_policy(claude.id)
    doc["roles"]["escalate"].insert(0, {"provider": "groq", "model": "openai/gpt-oss-120b"})
    _policy(tmp_path, monkeypatch, doc)
    rec = _Recorder(_openai_ok("openai/gpt-oss-120b"))

    resp = _post(_client(vault), rec, {"role": "escalate", "service_id": "cortex"})

    assert resp.status_code == 200
    assert resp.json()["provider"] == "groq"
    assert rec.anthropic_calls == 0


def test_a_claude_outage_falls_through_to_the_next_hop(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = _key(vault, "anthropic")
    _key(vault, "groq", priority=1)
    doc = _optin_policy(claude.id)
    doc["roles"]["escalate"].append({"provider": "groq", "model": "openai/gpt-oss-120b"})
    _policy(tmp_path, monkeypatch, doc)
    # 529 (overloaded) is presented as a retryable 503 and the walk moves on.
    rec = _Recorder(_fail(529, "overloaded"), _openai_ok("openai/gpt-oss-120b"))

    resp = _post(_client(vault), rec, {"role": "escalate", "service_id": "cortex"})

    assert resp.status_code == 200, resp.text
    assert rec.anthropic_calls == 1
    data = resp.json()
    assert data["provider"] == "groq"
    assert data["est_cost_usd"] == 0.0


# --- output-token floor on a capped paid hop ---------------------------------


def test_claude_gets_the_output_floor_not_the_1024_reservation_default(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = _key(vault, "anthropic")
    _policy(tmp_path, monkeypatch, _optin_policy(claude.id))
    rec = _Recorder(_claude_ok())

    resp = _post(_client(vault), rec, {"role": "escalate", "service_id": "cortex"})

    assert resp.status_code == 200, resp.text
    assert rec.bodies[0]["max_tokens"] == am.DEFAULT_MAX_TOKENS
    assert rec.bodies[0]["max_tokens"] > 1024


def test_a_smaller_named_budget_is_raised_to_the_floor(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = _key(vault, "anthropic")
    _policy(tmp_path, monkeypatch, _optin_policy(claude.id))
    rec = _Recorder(_claude_ok())

    resp = _post(
        _client(vault), rec, {"role": "escalate", "service_id": "cortex", "max_tokens": 100}
    )

    assert resp.status_code == 200, resp.text
    assert rec.bodies[0]["max_tokens"] == am.DEFAULT_MAX_TOKENS


def test_a_larger_named_budget_is_kept(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = _key(vault, "anthropic")
    _policy(tmp_path, monkeypatch, _optin_policy(claude.id))
    rec = _Recorder(_claude_ok())

    resp = _post(
        _client(vault), rec, {"role": "escalate", "service_id": "cortex", "max_tokens": 6000}
    )

    assert resp.status_code == 200, resp.text
    assert rec.bodies[0]["max_tokens"] == 6000


def test_the_reservation_covers_the_floor_so_a_tight_cap_refuses(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = _key(vault, "anthropic")
    # Worst case at the floor: 4096 x 15 / 1M = 0.061 USD. At the old 1024
    # default it would be 0.015 and this cap would wrongly admit the hop.
    _policy(tmp_path, monkeypatch, _optin_policy(claude.id, daily_usd=0.05))
    rec = _Recorder()

    resp = _post(_client(vault), rec, {"role": "escalate", "service_id": "cortex"})

    assert resp.status_code == 402
    assert resp.json()["error"]["cap"] == "key.daily_usd"
    assert rec.urls == []


def test_a_per_request_cap_below_the_floor_skips_claude_with_a_reason(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = _key(vault, "anthropic")
    _policy(tmp_path, monkeypatch, _optin_policy(claude.id, max_tokens_per_request=2048))
    rec = _Recorder()

    resp = _post(_client(vault), rec, {"role": "escalate", "service_id": "cortex"})

    assert resp.status_code == 502
    assert rec.urls == []
    details = resp.json()["error"]["details"]
    assert any("needs at least" in d and "output tokens" in d for d in details), details


# --- request shapes the adapter will not translate ---------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"tools": [{"type": "function", "function": {"name": "f", "parameters": {}}}]},
        {"n": 2},
        {"stream": True},
    ],
)
def test_unsupported_requests_are_a_typed_400_with_no_hold_or_call(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: dict[str, Any]
) -> None:
    claude = _key(vault, "anthropic")
    _policy(tmp_path, monkeypatch, _optin_policy(claude.id))
    rec = _Recorder()

    resp = _post(_client(vault), rec, {"role": "escalate", "service_id": "cortex", **extra})

    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "openvault_anthropic_unsupported"
    assert rec.urls == []


# --- secrets -----------------------------------------------------------------


def test_the_key_never_reaches_logs_errors_or_responses(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = _key(vault, "anthropic")
    _policy(tmp_path, monkeypatch, _optin_policy(claude.id))
    secret = _secret("anthropic")
    leaky = f"invalid x-api-key {secret}"
    outputs: list[str] = []

    with structlog.testing.capture_logs() as logs:
        ok = _post(_client(vault), _Recorder(_claude_ok()), {"role": "escalate"})
        reset_all_circuit_breakers()
        bad = _post(_client(vault), _Recorder(_fail(401, leaky)), {"role": "escalate"})
        reset_all_circuit_breakers()
        down = _post(_client(vault), _Recorder(_fail(500, leaky)), {"role": "escalate"})

    for resp in (ok, bad, down):
        outputs.append(resp.text)
    outputs.append(json.dumps(logs, default=str))

    assert ok.status_code == 200
    assert bad.status_code >= 400
    assert down.status_code >= 400
    for text in outputs:
        assert secret not in text


def test_adding_an_anthropic_key_does_not_bearer_probe_chat_completions(
    vault: KeyVault,
) -> None:
    from openmw.openvault.vault.key_add import add_tested_key

    result = add_tested_key(vault, "anthropic", "placeholder-anthropic-not-a-key")
    assert result.ok is False
    assert "no catalog chat model to test" in (result.error or "")
