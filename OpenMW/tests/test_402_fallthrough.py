"""OV-402-FALLTHROUGH-01: a per-hop 402 tries the next hop.

A cap refusal or a provider 402 skips that hop. The request is 402 only when
every hop is exhausted. Prices and caps stay as they are: an unpriced model
is still paid, and a hop over its cap is still not sent upstream.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.route.breaker import reset_all_circuit_breakers
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.hop_attempts import load_hop_attempts
from openmw.openvault.vault.route_policy import POLICY_ENV, reset_pending
from openmw.openvault.vault.store import KeyRecord, KeyVault
from openmw.openvault.vault.usage_store import UsageEvent, UsageStore

_HOSTS = {
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
}
_PAID = "gpt-4o-mini"
_FREE = "qwen/qwen3.8-27b"
_SECRET_MARK = "placeholder-not-a-live-key"


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


def _key(vault: KeyVault, provider: str) -> KeyRecord:
    record = vault.create(
        label=f"{provider}-0",
        provider=provider,
        secret=f"{_SECRET_MARK}-{provider}",
        role="primary",
        priority=0,
        base_url=_HOSTS[provider],
    )
    vault.set_precheck(record.id, status="ok", latency_ms=5.0, error=None)
    return record


def _policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, doc: dict[str, Any]) -> None:
    path = tmp_path / "route_policy.override.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv(POLICY_ENV, str(path))


def _ok(model: str) -> MagicMock:
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


def _fail(status: int, text: str = "upstream unavailable") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.headers = {}
    resp.json = MagicMock(return_value={"error": {"message": text}})
    return resp


class _Recorder:
    def __init__(self, *responses: MagicMock) -> None:
        self.urls: list[str] = []
        self._responses = list(responses)
        mock = MagicMock()
        mock.post = AsyncMock(side_effect=self._post)
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.aclose = AsyncMock(return_value=None)
        self.mock = mock

    async def _post(self, url: str, **kwargs: Any) -> MagicMock:
        self.urls.append(url)
        if not self._responses:
            raise AssertionError(f"unexpected upstream call {url}")
        return self._responses.pop(0)

    @property
    def providers(self) -> list[str]:
        found: list[str] = []
        for url in self.urls:
            match = [name for name, base in _HOSTS.items() if url.startswith(base)]
            found.append(match[0] if match else url)
        return found


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


def _sql(
    hops: list[dict[str, str]], *, models: dict[str, Any], caps: dict[str, Any]
) -> dict[str, Any]:
    return {"roles": {"sql": hops}, "models": models, "caps": caps}


def test_unpriced_paid_hop_falls_through_to_a_free_hop(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paid = _key(vault, "openai")
    _key(vault, "groq")
    _policy(
        tmp_path,
        monkeypatch,
        _sql(
            [
                {"provider": "openai", "model": _PAID},
                {"provider": "groq", "model": _FREE},
            ],
            models={},
            caps={"callers": {"cortex": {"daily_usd": 10}}},
        ),
    )
    rec = _Recorder(_ok(_FREE))
    resp = _post(_client(vault), rec, {"role": "sql", "service_id": "cortex"})

    assert resp.status_code == 200, resp.text
    assert rec.providers == ["groq"]
    data = resp.json()
    assert data["provider"] == "groq"
    assert data["model"] == _FREE
    assert data["est_cost_usd"] == 0.0
    rows = load_hop_attempts(vault.db_path)
    skipped = [row for row in rows if row.model == _PAID]
    assert len(skipped) == 1
    assert skipped[0].status == "402"
    assert skipped[0].reason == "caller.daily_usd"
    assert skipped[0].key_id == paid.id
    blob = " ".join([skipped[0].reason, skipped[0].status, skipped[0].model])
    assert _SECRET_MARK not in blob


def test_every_hop_refused_is_402_with_the_same_body(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _key(vault, "google")
    second = "gemini-3.1-flash-lite"
    _policy(
        tmp_path,
        monkeypatch,
        _sql(
            [
                {"provider": "openai", "model": _PAID},
                {"provider": "google", "model": second},
            ],
            models={
                f"openai/{_PAID}": {"free": False},
                f"google/{second}": {"free": False},
            },
            caps={"callers": {"cortex": {"daily_usd": 10}}},
        ),
    )
    rec = _Recorder()
    resp = _post(_client(vault), rec, {"role": "sql", "service_id": "cortex"})

    assert resp.status_code == 402, resp.text
    assert rec.urls == []
    assert resp.json() == {
        "error": {
            "message": (
                f"openai/{_PAID} has no price in the route policy, so "
                "caller.daily_usd cannot be checked"
            ),
            "type": "openvault_budget_exceeded",
            "reason": "budget_exceeded",
            "cap": "caller.daily_usd",
            "cap_detail": {
                "scope": "caller",
                "id": "cortex",
                "limit_name": "daily_usd",
                "limit": 10.0,
                "spent": 0.0,
                "requested": None,
                "unit": "usd",
                "provider": "openai",
                "model": _PAID,
            },
            "details": [
                f"openai-0: caller.daily_usd refuses {_PAID}",
                f"google-0: caller.daily_usd refuses {second}",
            ],
        },
        "model": None,
        "provider": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "est_cost_usd": "cost_unknown",
        "served_provider": None,
        "served_model": None,
        "served_local": False,
    }
    reasons = [(row.model, row.status, row.reason) for row in load_hop_attempts(vault.db_path)]
    assert reasons == [(_PAID, "402", "caller.daily_usd"), (second, "402", "caller.daily_usd")]


def test_cap_still_blocks_a_paid_hop_over_budget(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _key(vault, "groq")
    _policy(
        tmp_path,
        monkeypatch,
        _sql(
            [
                {"provider": "openai", "model": _PAID},
                {"provider": "groq", "model": _FREE},
            ],
            models={f"openai/{_PAID}": {"usd_per_1m_in": 0.15, "usd_per_1m_out": 0.6}},
            caps={"callers": {"cortex": {"daily_usd": 0.001}}},
        ),
    )
    UsageStore().record(
        UsageEvent(identity="127.0.0.1", tier="local", service_id="cortex", est_cost_usd=0.001)
    )
    client = _client(vault)
    rec = _Recorder(_ok(_FREE))
    resp = _post(client, rec, {"role": "sql", "service_id": "cortex"})

    assert resp.status_code == 200, resp.text
    assert rec.providers == ["groq"]
    skipped = [row for row in load_hop_attempts(vault.db_path) if row.model == _PAID]
    assert len(skipped) == 1
    assert skipped[0].status == "402"
    assert skipped[0].reason == "caller.daily_usd"

    _policy(
        tmp_path,
        monkeypatch,
        _sql(
            [{"provider": "openai", "model": _PAID}],
            models={f"openai/{_PAID}": {"usd_per_1m_in": 0.15, "usd_per_1m_out": 0.6}},
            caps={"callers": {"cortex": {"daily_usd": 0.001}}},
        ),
    )
    blocked = _post(client, _Recorder(), {"role": "sql", "service_id": "cortex"})
    assert blocked.status_code == 402, blocked.text
    err = blocked.json()["error"]
    assert err["cap"] == "caller.daily_usd"
    assert err["reason"] == "budget_exceeded"
    assert err["cap_detail"]["requested"] is not None
    assert err["cap_detail"]["requested"] > 0
    assert "no price" not in err["message"]


def test_provider_402_falls_through_to_the_next_hop(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _key(vault, "groq")
    _policy(
        tmp_path,
        monkeypatch,
        _sql(
            [
                {"provider": "openai", "model": _PAID},
                {"provider": "groq", "model": _FREE},
            ],
            models={f"openai/{_PAID}": {"usd_per_1m_in": 0.15, "usd_per_1m_out": 0.6}},
            caps={"callers": {"cortex": {"daily_usd": 10}}},
        ),
    )
    rec = _Recorder(_fail(402, "payment required"), _ok(_FREE))
    resp = _post(_client(vault), rec, {"role": "sql", "service_id": "cortex"})

    assert resp.status_code == 200, resp.text
    assert rec.providers == ["openai", "groq"]
    assert resp.json()["provider"] == "groq"
    rows = [(row.model, row.status, row.reason) for row in load_hop_attempts(vault.db_path)]
    assert (_PAID, "402", "credits_exhausted") in rows
    assert (_FREE, "200", "") in rows
