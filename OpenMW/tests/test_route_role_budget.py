"""ROUTE-ROLE-BUDGET-01 (#148): role routing, hard spend caps, response stamps.

No network and no real keys: upstream httpx is mocked, and every secret below
is a placeholder string. Caps and prices come from a temporary policy file
named by OPENVAULT_ROUTE_POLICY.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.route.breaker import reset_all_circuit_breakers
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.local_hop import inject_served_into_sse_chunk
from openmw.openvault.vault.providers import models_for
from openmw.openvault.vault.ratelimit import SseUsageCapture
from openmw.openvault.vault.route_policy import (
    BUDGET_ERROR_TYPE,
    BUDGET_EXCEEDED,
    DEFAULT_POLICY_PATH,
    POLICY_ENV,
    ROUTE_ROLES,
    ModelPrice,
    RoutePolicyError,
    SpendCap,
    SpendGuard,
    load_route_policy,
    parse_route_role,
    reset_pending,
    usage_pair,
)
from openmw.openvault.vault.store import KeyRecord, KeyVault
from openmw.openvault.vault.usage_store import UsageEvent, UsageStore

_HOSTS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "sambanova": "https://api.sambanova.ai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "sea_lion": "https://api.sea-lion.ai/v1",
    "openai": "https://api.openai.com/v1",
}
_ALLOWED_PROVIDERS = {"groq", "openrouter", "sambanova", "google", "nvidia", "sea_lion"}
_BANNED_NAMES = ("deepseek", "kimi", "moonshot", "glm", "z-ai", "minimax", "siliconflow")
_STAMPS = ("model", "provider", "tokens_in", "tokens_out", "est_cost_usd")
_PAID = "gpt-4o-mini"


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


def _policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, doc: dict[str, Any]) -> Path:
    path = tmp_path / "route_policy.override.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv(POLICY_ENV, str(path))
    return path


def _ok(model: str, prompt: int = 40, completion: int = 10) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = ""
    resp.headers = {}
    resp.json = MagicMock(
        return_value={
            "id": "chatcmpl-mock",
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        }
    )
    return resp


def _fail(status: int = 503) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = "upstream unavailable"
    resp.headers = {}
    resp.json = MagicMock(return_value={"error": "unavailable"})
    return resp


class _Recorder:
    def __init__(self, *responses: MagicMock) -> None:
        self.urls: list[str] = []
        self.bodies: list[dict[str, Any]] = []
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
        if not self._responses:
            raise AssertionError(f"unexpected upstream call {url}")
        return self._responses.pop(0)

    @property
    def providers(self) -> list[str]:
        found: list[str] = []
        for url in self.urls:
            match = [p for p, base in _HOSTS.items() if url.startswith(base)]
            found.append(match[0] if match else url)
        return found

    @property
    def models(self) -> list[str]:
        return [str(b.get("model")) for b in self.bodies]


def _client(vault: KeyVault) -> TestClient:
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def _post(
    client: TestClient,
    rec: _Recorder,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> Any:
    payload = {"model": "auto", "messages": [{"role": "user", "content": "hello"}], **body}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=rec.mock):
        return client.post("/v1/chat/completions", json=payload, headers=headers or {})


def _assert_stamped(data: dict[str, Any]) -> None:
    for name in _STAMPS:
        assert name in data, name


def _ledger() -> UsageStore:
    return UsageStore()


# --- policy file -------------------------------------------------------------


def test_default_policy_maps_every_role_to_free_allowed_models() -> None:
    policy = load_route_policy()
    assert set(policy.roles) == set(ROUTE_ROLES)
    for role in ROUTE_ROLES:
        hops = policy.hops_for(role)
        assert hops, role
        for hop in hops:
            assert hop.provider in _ALLOWED_PROVIDERS, hop
            assert policy.price_for(hop.provider, hop.model).free, hop
    assert "qwen" in policy.hops_for("sql")[0].model.lower()
    strong = {"openai/gpt-oss-120b", "gpt-oss-120b", "nvidia/nemotron-3-ultra-550b-a55b:free"}
    for role in ("sql", "tool", "summarize"):
        assert not strong & {h.model for h in policy.hops_for(role)}, role
    assert strong & {h.model for h in policy.hops_for("reason")}
    assert policy.max_tokens_per_request is None
    assert policy.key_caps == {}
    assert policy.caller_caps == {}


def test_default_policy_file_has_no_banned_model_names() -> None:
    text = DEFAULT_POLICY_PATH.read_text(encoding="utf-8").lower()
    for name in _BANNED_NAMES:
        assert name not in text, name


def test_unlisted_model_is_paid_and_unpriced() -> None:
    policy = load_route_policy()
    price = policy.price_for("openai", _PAID)
    assert price.free is False
    assert price.cost(1000, 1000) is None
    assert policy.price_for("openrouter", "some/paid-model").free is False
    assert policy.price_for("openrouter", "some/model:free").free is True


def test_override_file_layers_on_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _policy(
        tmp_path,
        monkeypatch,
        {
            "roles": {"sql": [{"provider": "openai", "model": _PAID}]},
            "models": {
                f"openai/{_PAID}": {"usd_per_1m_in": 0.15, "usd_per_1m_out": 0.6},
                "groq/*": {"free": False},
            },
            "caps": {
                "max_tokens_per_request": 2048,
                "keys": {"k1": {"monthly_usd": 5}},
                "callers": {"cortex": {"daily_usd": 1.5, "max_tokens_per_request": 512}},
            },
        },
    )
    policy = load_route_policy()
    assert [h.model for h in policy.hops_for("sql")] == [_PAID]
    assert policy.hops_for("tool") == load_route_policy().hops_for("tool")
    assert policy.price_for("groq", "anything").free is False
    assert policy.price_for("openai", _PAID).cost(1_000_000, 1_000_000) == pytest.approx(0.75)
    assert policy.key_caps["k1"] == SpendCap(monthly_usd=5.0)
    assert policy.caller_caps["cortex"].max_tokens_per_request == 512
    assert policy.max_tokens_per_request == 2048
    assert len(policy.sources) == 2


@pytest.mark.parametrize(
    "doc",
    [
        [],
        {"roles": {"chat": []}},
        {"roles": {"sql": "groq"}},
        {"roles": {"sql": [{"provider": "groq"}]}},
        {"roles": {"sql": [{"provider": " ", "model": "m"}]}},
        {"roles": []},
        {"models": []},
        {"models": {"groq/*": 1}},
        {"models": {"groq/*": {"free": "yes"}}},
        {"models": {"x/y": {"usd_per_1m_in": -1}}},
        {"models": {"x/y": {"usd_per_1m_in": "1"}}},
        {"caps": []},
        {"caps": {"keys": []}},
        {"caps": {"callers": {"c": 3}}},
        {"caps": {"callers": {"c": {"daily_usd": float("inf")}}}},
        {"caps": {"max_tokens_per_request": 0}},
        {"caps": {"max_tokens_per_request": True}},
    ],
)
def test_bad_override_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, doc: Any) -> None:
    _policy(tmp_path, monkeypatch, doc)
    with pytest.raises(RoutePolicyError):
        load_route_policy()


def test_unreadable_override_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(POLICY_ENV, str(tmp_path / "missing.json"))
    with pytest.raises(RoutePolicyError):
        load_route_policy()


def test_parse_route_role() -> None:
    assert parse_route_role(None) is None
    for role in ROUTE_ROLES:
        assert parse_route_role(role) == role
    for bad in ("", "SQL", "chat", 3, ["sql"]):
        with pytest.raises(ValueError):
            parse_route_role(bad)


def test_usage_pair_and_capture() -> None:
    assert usage_pair({"prompt_tokens": 3, "completion_tokens": 4}) == (3, 4)
    assert usage_pair({"prompt_tokens": True, "completion_tokens": 4}) == (None, None)
    assert usage_pair({"total_tokens": 7}) == (None, None)
    assert usage_pair(None) == (None, None)
    capture = SseUsageCapture()
    capture.feed(b'data: {"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n')
    capture.finish()
    assert (capture.prompt_tokens, capture.completion_tokens, capture.total_tokens) == (5, 2, 7)


# --- role routing ------------------------------------------------------------


@pytest.mark.parametrize("role", ROUTE_ROLES)
def test_role_sends_first_configured_model(vault: KeyVault, role: str) -> None:
    for provider in _ALLOWED_PROVIDERS:
        _key(vault, provider)
    first = load_route_policy().hops_for(role)[0]
    rec = _Recorder(_ok(first.model))
    resp = _post(_client(vault), rec, {"role": role, "service_id": "cortex"})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert rec.providers == [first.provider]
    assert rec.models == [first.model]
    assert "role" not in rec.bodies[0]
    assert "service_id" not in rec.bodies[0]
    assert data["model"] == first.model
    assert data["provider"] == first.provider
    assert data["tokens_in"] == 40
    assert data["tokens_out"] == 10
    assert data["est_cost_usd"] == 0.0


def test_role_falls_back_down_the_list_in_order(vault: KeyVault) -> None:
    for provider in ("groq", "openrouter", "sea_lion"):
        _key(vault, provider)
    hops = load_route_policy().hops_for("sql")
    rec = _Recorder(_fail(503), _fail(500), _ok(hops[2].model, 12, 3))
    resp = _post(_client(vault), rec, {"role": "sql"})

    assert resp.status_code == 200, resp.text
    assert rec.providers == [h.provider for h in hops]
    assert rec.models == [h.model for h in hops]
    data = resp.json()
    assert data["model"] == hops[2].model
    assert data["provider"] == hops[2].provider
    assert data["served_model"] == hops[2].model
    assert (data["tokens_in"], data["tokens_out"], data["est_cost_usd"]) == (12, 3, 0.0)
    assert _ledger().events()[-1]["model_served"] == hops[2].model
    spend = _ledger().spend_events()[-1]
    assert spend["route_role"] == "sql"
    assert spend["est_cost_usd"] == 0.0


def test_role_skips_providers_without_a_key(vault: KeyVault) -> None:
    _key(vault, "openrouter")
    hops = load_route_policy().hops_for("sql")
    rec = _Recorder(_ok(hops[1].model))
    resp = _post(_client(vault), rec, {"role": "sql"})
    assert resp.status_code == 200
    assert rec.providers == ["openrouter"]
    assert resp.json()["model"] == hops[1].model


def test_role_429_does_not_park_the_whole_key(vault: KeyVault) -> None:
    groq = _key(vault, "groq")
    _key(vault, "openrouter")
    hops = load_route_policy().hops_for("sql")
    limited = _fail(429)
    limited.text = "rate limit"
    rec = _Recorder(limited, _ok(hops[1].model))
    client = _client(vault)
    resp = _post(client, rec, {"role": "sql"})
    assert resp.status_code == 200
    assert rec.providers == ["groq", "openrouter"]
    tool_first = load_route_policy().hops_for("tool")[0]
    rec2 = _Recorder(_ok(tool_first.model))
    again = _post(client, rec2, {"role": "tool"})
    assert again.status_code == 200
    assert rec2.providers == ["groq"], f"groq key {groq.id[:8]} was parked whole"


def test_missing_role_keeps_the_pool_walk(vault: KeyVault) -> None:
    _key(vault, "groq")
    expected = models_for("groq")[0]
    rec = _Recorder(_ok(expected))
    resp = _post(_client(vault), rec, {})
    assert resp.status_code == 200
    assert rec.models == [expected]
    data = resp.json()
    _assert_stamped(data)
    assert data["model"] == expected
    assert data["provider"] == "groq"
    assert _ledger().spend_events()[-1]["route_role"] == ""


@pytest.mark.parametrize("bad", ["chat", "SQL", "", 7])
def test_unknown_role_is_a_named_422(vault: KeyVault, bad: Any) -> None:
    _key(vault, "groq")
    rec = _Recorder()
    resp = _post(_client(vault), rec, {"role": bad})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["type"] == "openvault_unknown_role"
    assert err["allowed"] == list(ROUTE_ROLES)
    assert err["role"] == bad
    _assert_stamped(resp.json())
    assert rec.urls == []


@pytest.mark.parametrize(
    ("extra", "headers", "flag"),
    [
        ({"strict": True}, {}, "strict"),
        ({}, {"X-OpenVault-Strict": "true"}, "strict"),
        ({"local_only": True}, {}, "local_only"),
    ],
)
def test_role_with_strict_or_local_only_is_a_named_422(
    vault: KeyVault, extra: dict[str, Any], headers: dict[str, str], flag: str
) -> None:
    _key(vault, "groq")
    rec = _Recorder()
    resp = _post(_client(vault), rec, {"role": "sql", **extra}, headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["type"] == "openvault_role_conflict"
    assert resp.json()["error"]["conflicts_with"] == flag
    assert rec.urls == []


def test_invalid_policy_fails_closed(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "groq")
    client = _client(vault)
    _policy(tmp_path, monkeypatch, {"caps": {"max_tokens_per_request": -5}})
    rec = _Recorder()
    resp = _post(client, rec, {"role": "sql"})
    assert resp.status_code == 503
    assert resp.json()["error"]["type"] == "openvault_route_policy_invalid"
    assert rec.urls == []


# --- paid models and caps ----------------------------------------------------


def _paid_sql(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    caps: dict[str, Any] | None = None,
    priced: bool = True,
) -> None:
    models: dict[str, Any] = {}
    if priced:
        models[f"openai/{_PAID}"] = {"usd_per_1m_in": 0.15, "usd_per_1m_out": 0.6}
    _policy(
        tmp_path,
        monkeypatch,
        {
            "roles": {
                "sql": [
                    {"provider": "groq", "model": "qwen/qwen3.8-27b"},
                    {"provider": "openai", "model": _PAID},
                ]
            },
            "models": models,
            "caps": caps or {},
        },
    )


def test_no_paid_fallback_without_a_cap(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "groq")
    _key(vault, "openai")
    _paid_sql(tmp_path, monkeypatch)
    rec = _Recorder(_fail(503))
    resp = _post(_client(vault), rec, {"role": "sql", "service_id": "cortex"})

    assert resp.status_code == 402, resp.text
    assert rec.providers == ["groq"]
    data = resp.json()
    err = data["error"]
    assert err["type"] == BUDGET_ERROR_TYPE
    assert err["reason"] == BUDGET_EXCEEDED
    assert err["cap"] == "paid_default"
    assert err["cap_detail"]["provider"] == "openai"
    assert err["cap_detail"]["model"] == _PAID
    assert data["model"] is None
    assert data["provider"] is None
    assert data["est_cost_usd"] == 0.0
    assert _ledger().events()[-1]["error_type"] == BUDGET_ERROR_TYPE


def test_paid_fallback_with_a_cap_serves_and_is_priced(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "groq")
    _key(vault, "openai")
    _paid_sql(tmp_path, monkeypatch, caps={"callers": {"cortex": {"daily_usd": 1.0}}})
    rec = _Recorder(_fail(503), _ok(_PAID, 1000, 500))
    resp = _post(_client(vault), rec, {"role": "sql", "service_id": "cortex"})

    assert resp.status_code == 200, resp.text
    assert rec.providers == ["groq", "openai"]
    data = resp.json()
    assert data["model"] == _PAID
    assert data["provider"] == "openai"
    assert data["est_cost_usd"] == pytest.approx((1000 * 0.15 + 500 * 0.6) / 1e6)
    row = _ledger().spend_events()[-1]
    assert row["service_id"] == "cortex"
    assert row["vault_key_id"]
    assert row["est_cost_usd"] == pytest.approx(data["est_cost_usd"])


def test_caller_daily_cap_hit_returns_budget_exceeded(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _paid_sql(tmp_path, monkeypatch, caps={"callers": {"cortex": {"daily_usd": 0.001}}})
    client = _client(vault)
    _ledger().record(
        UsageEvent(identity="127.0.0.1", tier="local", service_id="cortex", est_cost_usd=0.001)
    )
    rec = _Recorder()
    resp = _post(client, rec, {"role": "sql", "service_id": "cortex"})

    assert resp.status_code == 402, resp.text
    assert rec.urls == []
    err = resp.json()["error"]
    assert err["reason"] == BUDGET_EXCEEDED
    assert err["cap"] == "caller.daily_usd"
    detail = err["cap_detail"]
    assert detail["scope"] == "caller"
    assert detail["id"] == "cortex"
    assert detail["limit"] == 0.001
    assert detail["spent"] == pytest.approx(0.001)
    assert detail["requested"] > 0
    assert detail["unit"] == "usd"


def test_other_service_is_not_charged_for_cortex_spend(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _paid_sql(
        tmp_path,
        monkeypatch,
        caps={"callers": {"cortex": {"daily_usd": 0.001}, "airgpt": {"daily_usd": 0.001}}},
    )
    client = _client(vault)
    _ledger().record(
        UsageEvent(identity="127.0.0.1", tier="local", service_id="cortex", est_cost_usd=0.001)
    )
    rec = _Recorder(_ok(_PAID))
    resp = _post(client, rec, {"role": "sql", "service_id": "airgpt"})
    assert resp.status_code == 200, resp.text
    assert rec.providers == ["openai"]


def test_key_monthly_cap_names_the_vault_key(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paid = _key(vault, "openai")
    _paid_sql(tmp_path, monkeypatch, caps={"keys": {paid.id: {"monthly_usd": 0.5}}})
    client = _client(vault)
    _ledger().record(UsageEvent(identity="x", tier="free", vault_key_id=paid.id, est_cost_usd=0.5))
    rec = _Recorder()
    resp = _post(client, rec, {"role": "sql"})
    assert resp.status_code == 402
    err = resp.json()["error"]
    assert err["cap"] == "key.monthly_usd"
    assert err["cap_detail"]["id"] == paid.id[:8]
    assert rec.urls == []


def test_issued_key_is_its_own_caller_scope(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    client = _client(vault)
    key_id, headers = issue_key(client, tier="free")
    _paid_sql(
        tmp_path,
        monkeypatch,
        caps={"callers": {key_id: {"daily_usd": 0.0001}, "cortex": {"daily_usd": 100}}},
    )
    _ledger().record(
        UsageEvent(identity=key_id, tier="free", service_id=key_id, est_cost_usd=0.0001)
    )
    rec = _Recorder()
    # A keyed caller cannot borrow Cortex's budget by naming it.
    resp = _post(client, rec, {"role": "sql", "service_id": "cortex"}, headers)
    assert resp.status_code == 402, resp.text
    assert resp.json()["error"]["cap"] == "caller.daily_usd"
    assert resp.json()["error"]["cap_detail"]["id"] == key_id
    assert rec.urls == []


def test_unpriced_paid_model_under_a_cap_is_refused(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _paid_sql(tmp_path, monkeypatch, caps={"callers": {"cortex": {"daily_usd": 10}}}, priced=False)
    rec = _Recorder()
    resp = _post(_client(vault), rec, {"role": "sql", "service_id": "cortex"})
    assert resp.status_code == 402
    err = resp.json()["error"]
    assert err["cap"] == "caller.daily_usd"
    assert err["cap_detail"]["requested"] is None
    assert "no price" in err["message"]
    assert rec.urls == []


def test_caps_apply_without_role(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _paid_sql(tmp_path, monkeypatch, caps={"callers": {"cortex": {"monthly_usd": 0.002}}})
    client = _client(vault)
    _ledger().record(
        UsageEvent(identity="127.0.0.1", tier="local", service_id="cortex", est_cost_usd=0.002)
    )
    rec = _Recorder()
    resp = _post(client, rec, {"model": _PAID, "service_id": "cortex"})
    assert resp.status_code == 402
    assert resp.json()["error"]["cap"] == "caller.monthly_usd"
    assert rec.urls == []


def test_role_less_paid_without_cap_is_unchanged_and_unpriced(vault: KeyVault) -> None:
    _key(vault, "openai")
    rec = _Recorder(_ok(_PAID))
    resp = _post(_client(vault), rec, {"model": _PAID})
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "openai"
    assert data["model"] == _PAID
    assert data["est_cost_usd"] is None
    assert _ledger().spend_events()[-1]["est_cost_usd"] is None


def test_in_flight_reservations_count_against_the_cap() -> None:
    class _Ledger:
        def spend_usd(
            self, *, vault_key_id: str | None = None, service_id: str | None = None, since: float
        ) -> float:
            return 0.0

    policy = load_route_policy()
    policy = type(policy)(
        roles=policy.roles,
        models=((f"openai/{_PAID}", ModelPrice(False, 1.0, 1.0)), *policy.models),
        key_caps={},
        caller_caps={"cortex": SpendCap(daily_usd=0.0015)},
        max_tokens_per_request=None,
    )
    first = SpendGuard(policy=policy, ledger=_Ledger(), caller_id="cortex", route_role="sql")
    second = SpendGuard(policy=policy, ledger=_Ledger(), caller_id="cortex", route_role="sql")
    hop = {"provider": "openai", "model": _PAID, "key_id": "k", "prompt_tokens": 0}
    assert first.admit(**hop, max_output=1000) is None
    blocked = second.admit(**hop, max_output=1000)
    assert blocked is not None
    assert blocked.spent == pytest.approx(0.001)
    assert first.cost_for("openai", _PAID, None, None) == pytest.approx(0.001)
    first.release()
    assert second.admit(**hop, max_output=1000) is None
    second.release()
    second.release()
    assert first.cost_for("", "", None, None) == 0.0
    assert first.cost_for("openai", _PAID, None, None) is None


# --- per-request token cap ---------------------------------------------------


def test_request_over_the_token_cap_is_refused(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "groq")
    _policy(tmp_path, monkeypatch, {"caps": {"max_tokens_per_request": 256}})
    rec = _Recorder()
    resp = _post(_client(vault), rec, {"role": "sql", "max_tokens": 1000})
    assert resp.status_code == 402
    data = resp.json()
    assert data["error"]["reason"] == BUDGET_EXCEEDED
    assert data["error"]["cap"] == "request.max_tokens_per_request"
    assert data["error"]["cap_detail"]["limit"] == 256
    assert data["error"]["cap_detail"]["requested"] == 1000
    assert data["error"]["cap_detail"]["unit"] == "tokens"
    _assert_stamped(data)
    assert rec.urls == []


def test_token_cap_is_sent_when_the_caller_names_none(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "groq")
    _policy(
        tmp_path,
        monkeypatch,
        {
            "caps": {
                "max_tokens_per_request": 4096,
                "callers": {"cortex": {"max_tokens_per_request": 300}},
            }
        },
    )
    first = load_route_policy().hops_for("sql")[0]
    rec = _Recorder(_ok(first.model))
    resp = _post(_client(vault), rec, {"role": "sql", "service_id": "cortex"})
    assert resp.status_code == 200, resp.text
    assert rec.bodies[0]["max_tokens"] == 300
    over = _post(_client(vault), _Recorder(), {"service_id": "cortex", "max_tokens": 301})
    assert over.status_code == 402
    assert over.json()["error"]["cap_detail"]["id"] == "cortex"


def test_token_cap_beats_the_reasoning_floor(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "groq")
    _policy(tmp_path, monkeypatch, {"caps": {"max_tokens_per_request": 64}})
    rec = _Recorder()
    resp = _post(_client(vault), rec, {"role": "reason", "max_tokens": 32})
    assert rec.urls == []
    assert resp.status_code == 502
    _assert_stamped(resp.json())
    assert resp.json()["model"] is None


# --- stamps on every response ------------------------------------------------


def test_exhausted_walk_is_stamped(vault: KeyVault) -> None:
    _key(vault, "groq")
    rec = _Recorder(_fail(503), _fail(503), _fail(503))
    resp = _post(_client(vault), rec, {"role": "sql"})
    assert resp.status_code == 502
    data = resp.json()
    _assert_stamped(data)
    assert (data["model"], data["provider"], data["tokens_in"], data["est_cost_usd"]) == (
        None,
        None,
        0,
        0.0,
    )


def test_no_keys_is_stamped(vault: KeyVault) -> None:
    resp = _post(_client(vault), _Recorder(), {"role": "tool"})
    assert resp.status_code == 503
    _assert_stamped(resp.json())


def test_served_without_usage_reports_null_tokens(vault: KeyVault) -> None:
    _key(vault, "groq")
    first = load_route_policy().hops_for("sql")[0]
    bare = _ok(first.model)
    bare.json = MagicMock(return_value={"id": "x", "choices": []})
    resp = _post(_client(vault), _Recorder(bare), {"role": "sql"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == first.model
    assert data["tokens_in"] is None
    assert data["tokens_out"] is None
    assert data["est_cost_usd"] == 0.0


def test_stream_chunks_carry_stamps(vault: KeyVault) -> None:
    _key(vault, "groq")
    first = load_route_policy().hops_for("sql")[0]
    chunks = [
        b'data: {"id":"1","model":"upstream-name","choices":[{"delta":{"content":"hi"}}]}\n\n',
        b'data: {"id":"1","choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4,'
        b'"total_tokens":13}}\n\n',
        b"data: [DONE]\n\n",
    ]
    sent: list[dict[str, Any]] = []

    class _Resp:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {"content-type": "text/event-stream"}

        async def aiter_bytes(self) -> AsyncIterator[bytes]:
            for c in chunks:
                yield c

        async def aclose(self) -> None:
            return None

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def build_request(self, *args: Any, **kwargs: Any) -> object:
            sent.append(dict(kwargs.get("json") or {}))
            return object()

        async def send(self, request: object, *, stream: bool = False) -> _Resp:
            return _Resp()

        async def aclose(self) -> None:
            return None

    client = _client(vault)
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", _Client):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "role": "sql",
                "stream": True,
                "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 200
    assert sent[0]["model"] == first.model
    assert "role" not in sent[0]
    objs = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: {")]
    assert all(o["model"] == first.model and o["provider"] == "groq" for o in objs)
    usage_chunk = objs[-1]
    assert (usage_chunk["tokens_in"], usage_chunk["tokens_out"]) == (9, 4)
    assert usage_chunk["est_cost_usd"] == 0.0
    row = _ledger().events()[-1]
    assert (row["prompt_tokens"], row["completion_tokens"]) == (9, 4)
    assert _ledger().spend_events()[-1]["route_role"] == "sql"


def test_stream_refusal_is_stamped_json(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _paid_sql(tmp_path, monkeypatch)
    resp = _post(_client(vault), _Recorder(), {"role": "sql", "stream": True})
    assert resp.status_code == 402
    assert resp.json()["error"]["cap"] == "paid_default"
    _assert_stamped(resp.json())


def test_sse_injector_without_stamp_is_unchanged() -> None:
    chunk = b'data: {"id":"1"}\n'
    out = inject_served_into_sse_chunk(chunk, provider="groq", model="m", served_local=False)
    obj = json.loads(out.split(b"\n")[0][6:])
    assert "provider" not in obj
    assert obj["served_provider"] == "groq"


# --- ledger migration --------------------------------------------------------


def test_old_db_gains_route_spend_and_usage_events_keeps_18_columns(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    UsageStore(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE route_spend")
    store = UsageStore(db_path=db)
    with sqlite3.connect(db) as conn:
        usage_cols = conn.execute("PRAGMA table_info(usage_events)").fetchall()
        spend_cols = {r[1] for r in conn.execute("PRAGMA table_info(route_spend)")}
    assert len(usage_cols) == 18
    assert {"service_id", "vault_key_id", "route_role", "est_cost_usd"} <= spend_cols
    store.record(UsageEvent(identity="y", tier="free", service_id="svc", est_cost_usd=0.25))
    store.record(UsageEvent(identity="y", tier="free", vault_key_id="vk", est_cost_usd=None))
    assert store.spend_usd(service_id="svc", since=0) == pytest.approx(0.25)
    assert store.spend_usd(vault_key_id="vk", since=0) == 0.0
    assert store.spend_usd(since=0) == 0.0
    assert [r["est_cost_usd"] for r in store.spend_events()] == [0.25, None]


# --- PR #154 review: completions, output bound, lost ledger row, 503 detail --------

_CAP_ID = "cortex"
#: Worst case for one default-sized completion on the priced test model, in USD.
_ONE_COMPLETION = (3 * 0.15 + 1024 * 0.6) / 1e6


def _capped_paid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, daily_usd: float, *, priced: bool = True
) -> None:
    _paid_sql(
        tmp_path,
        monkeypatch,
        caps={"callers": {_CAP_ID: {"daily_usd": daily_usd}}},
        priced=priced,
    )


class _NoLedger:
    def spend_usd(
        self, *, vault_key_id: str | None = None, service_id: str | None = None, since: float
    ) -> float:
        return 0.0


def _unit_guard(daily_usd: float) -> SpendGuard:
    policy = load_route_policy()
    policy = type(policy)(
        roles=policy.roles,
        models=((f"openai/{_PAID}", ModelPrice(False, 1.0, 1.0)), *policy.models),
        key_caps={},
        caller_caps={_CAP_ID: SpendCap(daily_usd=daily_usd)},
        max_tokens_per_request=None,
    )
    return SpendGuard(policy=policy, ledger=_NoLedger(), caller_id=_CAP_ID, route_role="sql")


def _stream_client(sent: list[dict[str, Any]], chunks: list[bytes]) -> Any:
    class _Resp:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {"content-type": "text/event-stream"}

        async def aiter_bytes(self) -> AsyncIterator[bytes]:
            for c in chunks:
                yield c

        async def aclose(self) -> None:
            return None

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def build_request(self, *args: Any, **kwargs: Any) -> object:
            sent.append(json.loads(json.dumps(kwargs.get("json") or {})))
            return object()

        async def send(self, request: object, *, stream: bool = False) -> _Resp:
            return _Resp()

        async def aclose(self) -> None:
            return None

    return _Client


_USAGE_FRAME = (
    b'data: {"id":"1","choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4,'
    b'"total_tokens":13}}\n\n'
)
_STREAM = [
    b'data: {"id":"1","choices":[{"delta":{"content":"hi"}}]}\n\n',
    _USAGE_FRAME,
    b"data: [DONE]\n\n",
]
_STREAM_NO_USAGE = [_STREAM[0], _STREAM[2]]


def _stream_post(
    client: TestClient, sent: list[dict[str, Any]], chunks: list[bytes], body: dict[str, Any]
) -> Any:
    payload = {"messages": [{"role": "user", "content": "hello"}], "stream": True, **body}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", _stream_client(sent, chunks)):
        return client.post("/v1/chat/completions", json=payload)


def test_requested_completions_is_n_times_best_of() -> None:
    from openmw.openvault.vault.route_policy import MAX_COMPLETIONS, requested_completions

    assert requested_completions({}) == 1
    assert requested_completions({"n": None, "best_of": None}) == 1
    assert requested_completions({"n": 1}) == 1
    assert requested_completions({"n": 3}) == 3
    assert requested_completions({"best_of": 4}) == 4
    assert requested_completions({"n": 3, "best_of": 4}) == 12
    assert requested_completions({"n": MAX_COMPLETIONS}) == MAX_COMPLETIONS
    for bad in (0, -1, MAX_COMPLETIONS + 1, True, False, "2", 2.0, 2.5, [2], {"n": 2}):
        assert requested_completions({"n": bad}) is None, bad
        assert requested_completions({"best_of": bad}) is None, bad
    assert requested_completions({"n": 2, "best_of": 0}) is None


def test_admit_reserves_output_cost_for_every_completion() -> None:
    hop = {"provider": "openai", "model": _PAID, "key_id": "k", "prompt_tokens": 0}
    one = _unit_guard(1.0)
    assert one.admit(**hop, max_output=1000) is None
    assert one.held_usd == pytest.approx(0.001)
    one.release()

    many = _unit_guard(1.0)
    assert many.admit(**hop, max_output=1000, completions=12) is None
    assert many.held_usd == pytest.approx(0.012)
    assert many.holding
    many.release()
    assert not many.holding

    # 12 completions of 1000 tokens does not fit a 0.005 cap that one would.
    tight = _unit_guard(0.005)
    assert tight.admit(**hop, max_output=1000, completions=1) is None
    tight.release()
    refused = tight.admit(**hop, max_output=1000, completions=12)
    assert refused is not None
    assert refused.requested == pytest.approx(0.012)
    assert not tight.holding

    unbounded = _unit_guard(1.0)
    blocked = unbounded.admit(**hop, max_output=1000, completions=None)
    assert blocked is not None
    assert blocked.cap == "request.completions"
    assert blocked.to_body()["error"]["cap_detail"]["unit"] == "completions"
    assert not unbounded.holding


def test_n_128_on_a_capped_paid_hop_is_reserved_at_n_times_and_refused(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    # One completion fits with room to spare. 128 of them are about 0.079 USD.
    _capped_paid(tmp_path, monkeypatch, 0.01)
    rec = _Recorder()
    resp = _post(_client(vault), rec, {"role": "sql", "service_id": _CAP_ID, "n": 128})

    assert resp.status_code == 402, resp.text
    assert rec.urls == []
    err = resp.json()["error"]
    assert err["cap"] == "caller.daily_usd"
    assert err["cap_detail"]["requested"] == pytest.approx(128 * _ONE_COMPLETION, rel=0.01)
    assert _ledger().spend_events()[-1]["est_cost_usd"] == 0.0


def test_n_and_best_of_multiply_the_reservation_that_is_admitted(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    # Room for 3 completions (~0.0018) but not 4 (~0.0025).
    _capped_paid(tmp_path, monkeypatch, 0.002)
    client = _client(vault)

    over = _post(client, _Recorder(), {"role": "sql", "service_id": _CAP_ID, "n": 2, "best_of": 2})
    assert over.status_code == 402, over.text
    assert over.json()["error"]["cap_detail"]["requested"] == pytest.approx(
        4 * _ONE_COMPLETION, rel=0.01
    )

    rec = _Recorder(_ok(_PAID))
    fits = _post(client, rec, {"role": "sql", "service_id": _CAP_ID, "n": 3})
    assert fits.status_code == 200, fits.text
    assert rec.bodies[0]["n"] == 3


@pytest.mark.parametrize("bad", [0, -1, 129, "2", 2.0, True, [2]])
@pytest.mark.parametrize("field", ["n", "best_of"])
def test_unbounded_completion_count_is_refused_on_a_capped_paid_hop(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, bad: Any
) -> None:
    _key(vault, "openai")
    _capped_paid(tmp_path, monkeypatch, 100.0)
    rec = _Recorder()
    resp = _post(_client(vault), rec, {"role": "sql", "service_id": _CAP_ID, field: bad})

    assert resp.status_code == 402, resp.text
    assert rec.urls == []
    err = resp.json()["error"]
    assert err["reason"] == BUDGET_EXCEEDED
    assert err["cap"] == "request.completions"
    assert err["cap_detail"]["limit"] == 128
    assert err["cap_detail"]["unit"] == "completions"
    assert "traceback" not in resp.text.lower()


def test_free_hop_body_is_unchanged_by_the_spend_bounds(vault: KeyVault) -> None:
    _key(vault, "groq")
    first = load_route_policy().hops_for("sql")[0]
    rec = _Recorder(_ok(first.model))
    resp = _post(
        _client(vault),
        rec,
        {"role": "sql", "n": 128, "best_of": "x", "stream_options": {"include_obfuscation": False}},
    )
    assert resp.status_code == 200, resp.text
    body = rec.bodies[0]
    assert body["n"] == 128
    assert body["best_of"] == "x"
    assert "max_tokens" not in body
    assert "max_completion_tokens" not in body
    assert body["stream_options"] == {"include_obfuscation": False}


def test_pool_walk_without_a_cap_is_unchanged(vault: KeyVault) -> None:
    _key(vault, "openai")
    rec = _Recorder(_ok(_PAID))
    resp = _post(_client(vault), rec, {"n": 128})
    assert resp.status_code == 200, resp.text
    body = rec.bodies[0]
    assert body["n"] == 128
    assert "max_tokens" not in body


def test_capped_paid_hop_forwards_the_output_bound_it_reserved(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openmw.openvault.vault.ratelimit import DEFAULT_MAX_OUTPUT_TOKENS

    _key(vault, "openai")
    _capped_paid(tmp_path, monkeypatch, 100.0)
    client = _client(vault)
    ask = {"role": "sql", "service_id": _CAP_ID}

    none_named = _Recorder(_ok(_PAID))
    assert _post(client, none_named, ask).status_code == 200
    assert none_named.bodies[0]["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS
    assert "max_completion_tokens" not in none_named.bodies[0]

    named = _Recorder(_ok(_PAID))
    assert _post(client, named, {**ask, "max_tokens": 50}).status_code == 200
    assert named.bodies[0]["max_tokens"] == 50

    # The field name the caller chose is the one that is written, as
    # prepare_hop_body does. No max_tokens is invented beside it.
    completion = _Recorder(_ok(_PAID))
    assert _post(client, completion, {**ask, "max_completion_tokens": 300}).status_code == 200
    assert completion.bodies[0]["max_completion_tokens"] == 300
    assert "max_tokens" not in completion.bodies[0]

    # An invalid budget is deleted by prepare_hop_body, so the reservation falls
    # back to the default and that is what goes upstream.
    invalid = _Recorder(_ok(_PAID))
    assert _post(client, invalid, {**ask, "max_tokens": 0}).status_code == 200
    assert invalid.bodies[0]["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS


def test_bound_to_reservation_caps_a_field_above_the_hold() -> None:
    from openmw.openvault.vault.proxy import _bound_to_reservation

    above = {"max_tokens": 5000, "model": "m"}
    _bound_to_reservation(above, 1024, stream=False)
    assert above == {"max_tokens": 1024, "model": "m"}

    both = {"max_tokens": 5000, "max_completion_tokens": 7000}
    _bound_to_reservation(both, 64, stream=False)
    assert both == {"max_tokens": 64, "max_completion_tokens": 64}

    # A Responses-style field the chat endpoint ignores does not bound anything.
    ignored = {"max_output_tokens": 50}
    _bound_to_reservation(ignored, 50, stream=False)
    assert ignored["max_tokens"] == 50
    assert "stream_options" not in ignored


def test_stream_options_merge_and_the_callers_dict_is_not_mutated() -> None:
    from openmw.openvault.vault.proxy import _bound_to_reservation

    theirs = {"include_usage": False, "include_obfuscation": False}
    body: dict[str, Any] = {"stream_options": theirs}
    _bound_to_reservation(body, 100, stream=True)
    assert body["stream_options"] == {"include_usage": True, "include_obfuscation": False}
    assert theirs == {"include_usage": False, "include_obfuscation": False}

    for junk in ("yes", 3, None, ["include_usage"]):
        odd: dict[str, Any] = {"stream_options": junk}
        _bound_to_reservation(odd, 100, stream=True)
        assert odd["stream_options"] == {"include_usage": True}


def test_streamed_paid_hop_asks_for_usage_and_the_ledger_records_it(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openmw.openvault.vault.ratelimit import DEFAULT_MAX_OUTPUT_TOKENS

    _key(vault, "openai")
    _capped_paid(tmp_path, monkeypatch, 100.0)
    sent: list[dict[str, Any]] = []
    resp = _stream_post(
        _client(vault),
        sent,
        _STREAM,
        {"role": "sql", "service_id": _CAP_ID, "stream_options": {"include_obfuscation": False}},
    )
    assert resp.status_code == 200, resp.text
    assert sent[0]["stream_options"] == {"include_obfuscation": False, "include_usage": True}
    assert sent[0]["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS

    # The client never asked for usage, and the row is the measured one, not the hold.
    measured = (9 * 0.15 + 4 * 0.6) / 1e6
    row = _ledger().spend_events()[-1]
    assert row["est_cost_usd"] == pytest.approx(measured)
    event = _ledger().events()[-1]
    assert (event["prompt_tokens"], event["completion_tokens"]) == (9, 4)
    assert not event["estimated"]


def test_streamed_free_hop_body_is_unchanged(vault: KeyVault) -> None:
    _key(vault, "groq")
    sent: list[dict[str, Any]] = []
    resp = _stream_post(_client(vault), sent, _STREAM_NO_USAGE, {"role": "sql"})
    assert resp.status_code == 200, resp.text
    assert "stream_options" not in sent[0]
    assert "max_tokens" not in sent[0]


def test_lost_ledger_row_keeps_the_hold_so_the_cap_still_binds(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    # One reservation (~0.0006) fits. Two do not, and a measured 40+10 would not
    # use the cap up, so only a surviving hold can refuse the second request.
    _capped_paid(tmp_path, monkeypatch, 0.001)
    client = _client(vault)
    ask = {"role": "sql", "service_id": _CAP_ID}

    with patch.object(UsageStore, "record", side_effect=sqlite3.OperationalError("disk I/O")):
        first = _post(client, _Recorder(_ok(_PAID)), ask)
    assert first.status_code == 200, first.text
    assert first.json()["provider"] == "openai"

    rec = _Recorder()
    second = _post(client, rec, ask)
    assert second.status_code == 402, second.text
    assert rec.urls == []
    err = second.json()["error"]
    assert err["cap"] == "caller.daily_usd"
    assert err["cap_detail"]["spent"] == pytest.approx(_ONE_COMPLETION, rel=0.01)


def test_written_ledger_row_still_releases_the_hold(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _capped_paid(tmp_path, monkeypatch, 0.001)
    client = _client(vault)
    ask = {"role": "sql", "service_id": _CAP_ID}

    assert _post(client, _Recorder(_ok(_PAID)), ask).status_code == 200
    again = _post(client, _Recorder(_ok(_PAID)), ask)
    assert again.status_code == 200, again.text


def test_lost_ledger_row_logs_an_error_without_key_material(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from structlog.testing import capture_logs

    record = _key(vault, "openai")
    _capped_paid(tmp_path, monkeypatch, 0.001)
    with (
        capture_logs() as logs,
        patch.object(UsageStore, "record", side_effect=sqlite3.OperationalError("disk I/O")),
    ):
        resp = _post(_client(vault), _Recorder(_ok(_PAID)), {"role": "sql", "service_id": _CAP_ID})
    assert resp.status_code == 200, resp.text

    kept = [e for e in logs if e["event"] == "usage_ledger_write_failed_hold_kept"]
    assert len(kept) == 1
    assert kept[0]["log_level"] == "error"
    assert kept[0]["provider"] == "openai"
    assert kept[0]["held_usd"] == pytest.approx(_ONE_COMPLETION, rel=0.01)
    dump = json.dumps(logs, default=str)
    assert "placeholder-openai" not in dump
    assert record.id not in dump


def test_lost_ledger_row_for_a_hop_that_served_nothing_does_not_leak_a_hold(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _capped_paid(tmp_path, monkeypatch, 0.001)
    client = _client(vault)
    ask = {"role": "sql", "service_id": _CAP_ID}

    with patch.object(UsageStore, "record", side_effect=sqlite3.OperationalError("disk I/O")):
        failed = _post(client, _Recorder(_fail(503)), ask)
    assert failed.status_code >= 500

    # Nothing was served, so nothing was spent and nothing may stay held.
    ok = _post(client, _Recorder(_ok(_PAID)), ask)
    assert ok.status_code == 200, ok.text


def test_unreadable_policy_503_does_not_echo_the_path(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from structlog.testing import capture_logs

    _key(vault, "groq")
    secret_dir = tmp_path / "operator-private-dir"
    missing = secret_dir / "policy-that-is-not-there.json"
    monkeypatch.setenv(POLICY_ENV, str(missing))

    with capture_logs() as logs:
        resp = _post(_client(vault), _Recorder(), {"role": "sql"})
    assert resp.status_code == 503
    err = resp.json()["error"]
    assert err["type"] == "openvault_route_policy_invalid"
    assert err["message"] == "cannot read route policy policy-that-is-not-there.json"
    assert "operator-private-dir" not in resp.text
    assert str(tmp_path) not in resp.text
    assert "Errno" not in resp.text

    detail = [e for e in logs if e["event"] == "route_policy_unreadable"]
    assert detail
    assert "operator-private-dir" in detail[0]["path"]
