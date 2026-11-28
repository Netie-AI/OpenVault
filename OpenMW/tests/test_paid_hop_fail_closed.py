"""Readiness sim 2026-11-28: uncapped paid hops, downgrade stamps, cost_unknown.

G1: a key with no cap entry that omits role was served on a paid hop with no
ceiling (5/5). role=reason was already refused. Both are 402 paid_default now.

G10: a paid-then-free role that hits the paid cap used to return 200 from the
free hop with no signal. The response, the headers, and the ledger now say
served_downgraded with the from and to hops.

G5: an unpriced paid model is cost_unknown, not null and not 0.

No live keys. Every secret below is a placeholder. Caps and prices in the
shipped policy are not raised.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.paths import keys_db_path
from openmw.openvault.route.breaker import reset_all_circuit_breakers
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.route_policy import (
    BUDGET_EXCEEDED,
    COST_UNKNOWN,
    POLICY_ENV,
    ModelPrice,
    RoutePolicy,
    SpendCap,
    SpendGuard,
    reset_pending,
)
from openmw.openvault.vault.store import KeyRecord, KeyVault
from openmw.openvault.vault.usage_store import UsageEvent, UsageStore

_HOSTS = {
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
}
_PAID = "gpt-4o-mini"
_FREE = "qwen/qwen3.8-27b"
_SECRET = "placeholder-not-a-live-key"
_PRICE = {"usd_per_1m_in": 0.15, "usd_per_1m_out": 0.6}


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
        secret=f"{_SECRET}-{provider}",
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


def _client(vault: KeyVault) -> TestClient:
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def _post(client: TestClient, rec: _Recorder, body: dict[str, Any]) -> Any:
    payload = {"messages": [{"role": "user", "content": "hello"}], **body}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=rec.mock):
        return client.post("/v1/chat/completions", json=payload)


def _priced_openai(*, caps: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "models": {f"openai/{_PAID}": dict(_PRICE)},
        "caps": caps or {},
    }


def _assert_no_secret(resp: Any) -> None:
    blob = resp.text + " " + " ".join(f"{k}:{v}" for k, v in resp.headers.items())
    assert _SECRET not in blob


def test_admit_without_role_refuses_uncapped_paid_five_times() -> None:
    """The gate itself, before HTTP: five admits, five refusals, no hold."""

    class _Ledger:
        def spend_usd(
            self, *, vault_key_id: str | None = None, service_id: str | None = None, since: float
        ) -> float:
            return 0.0

    policy = RoutePolicy(
        roles={},
        models=((f"openai/{_PAID}", ModelPrice(False, 0.15, 0.6)),),
        key_caps={},
        caller_caps={},
        max_tokens_per_request=None,
    )
    refused = 0
    for _ in range(5):
        guard = SpendGuard(policy=policy, ledger=_Ledger(), caller_id="cortex", route_role=None)
        block = guard.admit(
            provider="openai",
            model=_PAID,
            key_id="vault-key",
            prompt_tokens=10,
            max_output=20,
        )
        assert block is not None
        assert block.cap == "paid_default"
        assert block.scope == "paid_default"
        assert not guard.holding
        refused += 1
    assert refused == 5

    allowed = SpendGuard(
        policy=RoutePolicy(
            roles={},
            models=policy.models,
            key_caps={},
            caller_caps={"cortex": SpendCap(daily_usd=10.0)},
            max_tokens_per_request=None,
        ),
        ledger=_Ledger(),
        caller_id="cortex",
        route_role=None,
    )
    assert (
        allowed.admit(
            provider="openai", model=_PAID, key_id="vault-key", prompt_tokens=10, max_output=20
        )
        is None
    )
    assert allowed.holding
    allowed.release()


def test_uncapped_paid_hop_without_role_is_refused_five_of_five(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G1 regression. The sim served this hop 5/5 when role was omitted."""
    _key(vault, "openai")
    _policy(tmp_path, monkeypatch, _priced_openai())
    client = _client(vault)
    rec = _Recorder(*[_ok(_PAID) for _ in range(5)])
    statuses: list[int] = []
    for _ in range(5):
        resp = _post(client, rec, {"model": _PAID})
        statuses.append(resp.status_code)
        assert resp.status_code == 402, resp.text
        err = resp.json()["error"]
        assert err["reason"] == BUDGET_EXCEEDED
        assert err["cap"] == "paid_default"
        assert "paid model" in err["message"]
        assert "no USD cap" in err["message"]
        # Price is known and nothing was sent, so the spend is zero, not unknown.
        assert resp.json()["est_cost_usd"] == 0.0
        assert "served_downgraded" not in resp.json()
        _assert_no_secret(resp)
    assert statuses == [402, 402, 402, 402, 402]
    assert rec.urls == []
    events = UsageStore().events()
    assert [row["status"] for row in events] == [402, 402, 402, 402, 402]
    assert all(row["model_served"] == "" for row in events)
    spend = UsageStore().spend_events()
    assert [row["est_cost_usd"] for row in spend] == [0.0, 0.0, 0.0, 0.0, 0.0]
    assert all(row["cost_status"] == "" for row in spend)


def test_role_reason_without_a_cap_is_still_refused(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _policy(
        tmp_path,
        monkeypatch,
        {
            "roles": {"reason": [{"provider": "openai", "model": _PAID}]},
            "models": {f"openai/{_PAID}": dict(_PRICE)},
            "caps": {},
        },
    )
    rec = _Recorder(_ok(_PAID))
    resp = _post(_client(vault), rec, {"role": "reason"})
    assert resp.status_code == 402, resp.text
    assert rec.urls == []
    assert resp.json()["error"]["cap"] == "paid_default"
    assert resp.json()["error"]["reason"] == BUDGET_EXCEEDED
    _assert_no_secret(resp)


def test_priced_paid_hop_with_a_cap_still_serves_without_role(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _policy(tmp_path, monkeypatch, _priced_openai(caps={"callers": {"cortex": {"daily_usd": 5.0}}}))
    rec = _Recorder(_ok(_PAID))
    resp = _post(_client(vault), rec, {"model": _PAID, "service_id": "cortex"})
    assert resp.status_code == 200, resp.text
    assert len(rec.urls) == 1
    data = resp.json()
    assert data["provider"] == "openai"
    assert data["model"] == _PAID
    assert data["est_cost_usd"] == pytest.approx((40 * 0.15 + 10 * 0.6) / 1_000_000)
    assert data["est_cost_usd"] != COST_UNKNOWN
    assert "served_downgraded" not in data
    _assert_no_secret(resp)


def test_unpriced_paid_model_is_cost_unknown_not_null_or_zero(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _policy(tmp_path, monkeypatch, {"models": {}, "caps": {}})
    rec = _Recorder(_ok(_PAID))
    resp = _post(_client(vault), rec, {"model": _PAID})
    assert resp.status_code == 402, resp.text
    assert rec.urls == []
    cost = resp.json()["est_cost_usd"]
    assert cost == COST_UNKNOWN
    assert cost is not None
    assert cost != 0
    assert cost != 0.0
    row = UsageStore().spend_events()[-1]
    assert row["est_cost_usd"] == COST_UNKNOWN
    assert row["cost_status"] == COST_UNKNOWN
    assert row["service_id"]
    assert UsageStore().spend_usd(service_id=row["service_id"], since=0) == 0.0
    with sqlite3.connect(keys_db_path()) as conn:
        raw = conn.execute(
            "SELECT est_cost_usd, cost_status FROM route_spend ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert raw[0] is None
    assert raw[1] == COST_UNKNOWN
    _assert_no_secret(resp)


def test_paid_then_free_cap_hit_stamps_the_downgrade(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _key(vault, "groq")
    _policy(
        tmp_path,
        monkeypatch,
        {
            "roles": {
                "sql": [
                    {"provider": "openai", "model": _PAID},
                    {"provider": "groq", "model": _FREE},
                ]
            },
            "models": {f"openai/{_PAID}": dict(_PRICE), "groq/*": {"free": True}},
            "caps": {"callers": {"cortex": {"daily_usd": 0.001}}},
        },
    )
    UsageStore().record(
        UsageEvent(identity="127.0.0.1", tier="local", service_id="cortex", est_cost_usd=0.001)
    )
    rec = _Recorder(_ok(_FREE))
    resp = _post(_client(vault), rec, {"role": "sql", "service_id": "cortex"})

    assert resp.status_code == 200, resp.text
    assert rec.urls == [f"{_HOSTS['groq']}/chat/completions"]
    data = resp.json()
    assert data["served_downgraded"] is True
    assert data["downgraded_from"] == f"openai/{_PAID}"
    assert data["downgraded_to"] == f"groq/{_FREE}"
    assert data["provider"] == "groq"
    assert data["model"] == _FREE
    assert data["est_cost_usd"] == 0.0
    assert resp.headers["x-openvault-served-downgraded"] == "true"
    assert resp.headers["x-openvault-downgraded-from"] == f"openai/{_PAID}"
    assert resp.headers["x-openvault-downgraded-to"] == f"groq/{_FREE}"
    event = UsageStore().events()[-1]
    assert event["status"] == 200
    assert event["served_downgraded"] is True
    assert event["downgraded_from"] == f"openai/{_PAID}"
    assert event["downgraded_to"] == f"groq/{_FREE}"
    spend = UsageStore().spend_events()[-1]
    assert spend["served_downgraded"] is True
    assert spend["downgraded_from"] == f"openai/{_PAID}"
    assert spend["downgraded_to"] == f"groq/{_FREE}"
    assert spend["est_cost_usd"] == 0.0
    _assert_no_secret(resp)


def test_paid_then_free_stream_downgrade_is_on_the_header_and_chunks(
    vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(vault, "openai")
    _key(vault, "groq")
    _policy(
        tmp_path,
        monkeypatch,
        {
            "roles": {
                "sql": [
                    {"provider": "openai", "model": _PAID},
                    {"provider": "groq", "model": _FREE},
                ]
            },
            "models": {f"openai/{_PAID}": dict(_PRICE), "groq/*": {"free": True}},
            "caps": {"callers": {"cortex": {"daily_usd": 0.001}}},
        },
    )
    UsageStore().record(
        UsageEvent(identity="127.0.0.1", tier="local", service_id="cortex", est_cost_usd=0.001)
    )
    chunks = [
        b'data: {"id":"1","choices":[{"delta":{"content":"ok"}}]}\n\n',
        b'data: {"id":"1","choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2,'
        b'"total_tokens":6}}\n\n',
        b"data: [DONE]\n\n",
    ]
    urls: list[str] = []

    class _Resp:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {"content-type": "text/event-stream"}

        async def aiter_bytes(self) -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        async def aclose(self) -> None:
            return None

    class _StreamClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def build_request(self, *args: Any, **kwargs: Any) -> object:
            urls.append(str(args[1] if len(args) > 1 else kwargs.get("url")))
            return object()

        async def send(self, request: object, *, stream: bool = False) -> _Resp:
            return _Resp()

        async def aclose(self) -> None:
            return None

    client = _client(vault)
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", _StreamClient):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "role": "sql",
                "service_id": "cortex",
                "stream": True,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert resp.status_code == 200, resp.text
    assert urls == [f"{_HOSTS['groq']}/chat/completions"]
    assert resp.headers["x-openvault-served-downgraded"] == "true"
    assert resp.headers["x-openvault-downgraded-from"] == f"openai/{_PAID}"
    assert resp.headers["x-openvault-downgraded-to"] == f"groq/{_FREE}"
    objs = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: {")]
    assert len(objs) == 2
    assert all(obj["served_downgraded"] is True for obj in objs)
    assert objs[0]["downgraded_from"] == f"openai/{_PAID}"
    assert objs[0]["downgraded_to"] == f"groq/{_FREE}"
    assert objs[-1]["est_cost_usd"] == 0.0
    event = UsageStore().events()[-1]
    assert event["stream"] is True
    assert event["served_downgraded"] is True
    assert event["downgraded_from"] == f"openai/{_PAID}"
    assert event["downgraded_to"] == f"groq/{_FREE}"
    _assert_no_secret(resp)


def test_free_hop_does_not_claim_a_downgrade(vault: KeyVault) -> None:
    _key(vault, "groq")
    rec = _Recorder(_ok(_FREE))
    resp = _post(_client(vault), rec, {"role": "sql"})
    assert resp.status_code == 200, resp.text
    assert "served_downgraded" not in resp.json()
    names = {key.lower() for key in resp.headers}
    assert "x-openvault-served-downgraded" not in names
    _assert_no_secret(resp)


def test_old_ledger_gains_downgrade_and_cost_status(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE usage_events (
              event_id TEXT PRIMARY KEY,
              created_at REAL NOT NULL,
              identity TEXT NOT NULL,
              tier TEXT NOT NULL,
              api_key_id TEXT,
              model_requested TEXT NOT NULL DEFAULT '',
              model_served TEXT NOT NULL DEFAULT '',
              provider TEXT NOT NULL DEFAULT '',
              vault_key_id TEXT NOT NULL DEFAULT '',
              prompt_tokens INTEGER NOT NULL DEFAULT 0,
              completion_tokens INTEGER NOT NULL DEFAULT 0,
              total_tokens INTEGER NOT NULL DEFAULT 0,
              estimated INTEGER NOT NULL DEFAULT 0,
              cache_hit INTEGER NOT NULL DEFAULT 0,
              stream INTEGER NOT NULL DEFAULT 0,
              status INTEGER NOT NULL DEFAULT 0,
              error_type TEXT NOT NULL DEFAULT '',
              latency_ms INTEGER NOT NULL DEFAULT 0,
              limited_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE route_spend (
              event_id TEXT PRIMARY KEY,
              created_at REAL NOT NULL,
              service_id TEXT NOT NULL DEFAULT '',
              vault_key_id TEXT NOT NULL DEFAULT '',
              route_role TEXT NOT NULL DEFAULT '',
              est_cost_usd REAL
            )
            """
        )
    store = UsageStore(db_path=db)
    with sqlite3.connect(db) as conn:
        usage_cols = {row[1] for row in conn.execute("PRAGMA table_info(usage_events)")}
        spend_cols = {row[1] for row in conn.execute("PRAGMA table_info(route_spend)")}
    assert {"served_downgraded", "downgraded_from", "downgraded_to", "cost_status"} <= usage_cols
    assert {"served_downgraded", "downgraded_from", "downgraded_to", "cost_status"} <= spend_cols
    store.record(UsageEvent(identity="y", tier="free", service_id="svc", est_cost_usd=0.25))
    store.record(
        UsageEvent(
            identity="y",
            tier="free",
            service_id="svc",
            est_cost_usd=0.0,
            cost_status=COST_UNKNOWN,
            status=402,
        )
    )
    assert store.spend_usd(service_id="svc", since=0) == pytest.approx(0.25)
    assert [row["est_cost_usd"] for row in store.spend_events()] == [0.25, COST_UNKNOWN]
    with sqlite3.connect(db) as conn:
        raw = conn.execute(
            "SELECT est_cost_usd, cost_status FROM route_spend ORDER BY created_at"
        ).fetchall()
    assert raw[1][0] is None
    assert raw[1][1] == COST_UNKNOWN
