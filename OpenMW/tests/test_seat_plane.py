"""Seat plane: flag off stays byte-identical. Flag on is tenant scope.

No network. No printed tokens. OV_SEAT_PLANE must be the digit 1.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.seat_plane import (
    DEFAULT_MARKUP,
    MANAGER_HEADER,
    overage_usd,
    reset_seat_policy_cache,
    seat_plane_enabled,
)
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.system_plane import EntitlementStore, metering_overlay
from openmw.openvault.vault.usage_store import UsageEvent, UsageStore

_CHAT = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.delenv("OV_SEAT_PLANE", raising=False)
    monkeypatch.delenv("OV_SEAT_POLICY", raising=False)
    monkeypatch.delenv("OPENVAULT_ROUTE_POLICY", raising=False)
    reset_seat_policy_cache()
    return root


@pytest.fixture
def vault(home: Path) -> KeyVault:
    return KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))


@pytest.fixture
def client(vault: KeyVault) -> TestClient:
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def _account(client: TestClient, name: str) -> dict[str, Any]:
    res = client.post(
        "/api/accounts",
        json={"display_name": name, "auth_provider": "netie_email", "local_part": name},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _tenant(client: TestClient, account_id: str, label: str) -> dict[str, Any]:
    res = client.post(
        "/api/system/tenant/tenants",
        json={"account_id": account_id, "label": label},
    )
    assert res.status_code == 200, res.text
    return res.json()["tenant"]


def _seat(client: TestClient, tenant_id: str, label: str) -> dict[str, Any]:
    res = client.post(
        "/api/system/tenant/seats",
        json={"tenant_id": tenant_id, "label": label},
    )
    assert res.status_code == 200, res.text
    return res.json()["seat"]


def _manager(client: TestClient, tenant_id: str) -> tuple[str, dict[str, str]]:
    res = client.post(
        "/api/system/tenant/managers",
        json={"tenant_id": tenant_id, "label": "desk"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    return body["manager_id"], {MANAGER_HEADER: body["token"]}


def _bind(client: TestClient, seat_id: str, api_key_id: str) -> None:
    res = client.post(
        "/api/system/tenant/bind",
        json={"seat_id": seat_id, "api_key_id": api_key_id},
    )
    assert res.status_code == 200, res.text


def _record(
    store: UsageStore,
    api_key_id: str,
    *,
    created_at: float,
    cost: float | None,
    role: str = "sql",
    total: int = 10,
    cache_hit: bool = False,
    status: int = 200,
) -> None:
    store.record(
        UsageEvent(
            identity=api_key_id,
            tier="free",
            api_key_id=api_key_id,
            model_requested="m",
            model_served="m",
            provider="groq",
            total_tokens=total,
            prompt_tokens=total,
            est_cost_usd=cost,
            route_role=role,
            service_id=api_key_id,
            created_at=created_at,
            cache_hit=cache_hit,
            status=status,
        )
    )


def _healthy(vault: KeyVault) -> None:
    rec = vault.create(
        label="groq-hop",
        provider="groq",
        secret="sk-test-seat-plane-aaaaaaaa",
        role="primary",
        priority=0,
        base_url="https://example.invalid/v1",
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)


def _mock_ok() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "{}"
    resp.headers = {}
    resp.json = MagicMock(
        return_value={
            "id": "chatcmpl-1",
            "choices": [],
            "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        }
    )
    mock = MagicMock()
    mock.post = AsyncMock(return_value=resp)
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


def test_flag_is_digit_1_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OV_SEAT_PLANE", "1")
    assert seat_plane_enabled() is True
    monkeypatch.setenv("OV_SEAT_PLANE", " 1 ")
    assert seat_plane_enabled() is True
    for value in ("", "0", "true", "yes", "on", "01", "1.0", "2"):
        monkeypatch.setenv("OV_SEAT_PLANE", value)
        assert seat_plane_enabled() is False
    assert overage_usd(10, 4, DEFAULT_MARKUP) == pytest.approx(7.2)
    assert overage_usd(3, 4, 1.2) == 0.0
    assert overage_usd(5, 0, 2) == 10.0


def test_flag_off_metering_and_usage_are_byte_identical(
    client: TestClient, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acct = _account(client, "byteco")
    client.post("/api/system/unlock", json={"account_id": acct["id"], "plan_id": "pro"})
    store = UsageStore(db_path=home / "keys.db")
    _record(store, "loop", created_at=1_700_000_000.0, cost=1.5, total=8)
    names = [
        row[0]
        for row in sqlite3.connect(home / "keys.db").execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    ]
    assert "usage_counters" not in names
    assert "seat_tenants" not in names

    def grab(header: dict[str, str] | None = None) -> tuple[bytes, bytes]:
        meter = client.get(
            "/api/system/metering",
            params={"account_id": acct["id"]},
            headers=header,
        )
        usage = client.get("/api/usage", headers=header)
        assert meter.status_code == 200
        assert usage.status_code == 200
        return meter.content, usage.content

    base_meter, base_usage = grab()
    expected_meter = _json_bytes(
        metering_overlay(EntitlementStore().get(acct["id"]), UsageStore().summary())
    )
    usage_store = UsageStore()
    events = usage_store.events()
    expected_usage = _json_bytes(
        {
            "ok": True,
            "events": events,
            "count": len(events),
            "summary": usage_store.summary(),
        }
    )
    assert base_meter == expected_meter
    assert base_usage == expected_usage
    assert b'"ledger_scope":"process"' in base_meter
    for value in ("", "0", "true", "yes", "on", "2"):
        monkeypatch.setenv("OV_SEAT_PLANE", value)
        meter, usage = grab({MANAGER_HEADER: "ovm_not-a-manager"})
        assert meter == base_meter
        assert usage == base_usage


def test_keyed_chat_skips_summary_only_when_flag_is_on(
    client: TestClient, vault: KeyVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    _healthy(vault)
    _key_id, headers = issue_key(client, label="counter-seat")
    calls = {"n": 0}
    real = UsageStore.summary

    def _wrapped(self: UsageStore, **kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        return real(self, **kwargs)

    monkeypatch.setattr(UsageStore, "summary", _wrapped)
    mock = _mock_ok()
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        flagged_off = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
    assert flagged_off.status_code == 200
    assert calls["n"] >= 1

    calls["n"] = 0
    monkeypatch.setenv("OV_SEAT_PLANE", "1")
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        first = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        second = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["n"] == 0
    store = UsageStore()
    counted = store.counter_billable(_key_id)
    assert counted == store.summary(api_key_id=_key_id)["billable_tokens"]
    assert counted > 0


def test_manager_sees_only_its_tenant(
    client: TestClient, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OV_SEAT_PLANE", "1")
    left_account = _account(client, "leftco")
    right_account = _account(client, "rightco")
    left = _tenant(client, left_account["id"], "Left")
    right = _tenant(client, right_account["id"], "Right")
    left_seat = _seat(client, left["tenant_id"], "l1")
    right_seat = _seat(client, right["tenant_id"], "r1")
    left_key, _left_headers = issue_key(client, label="left-key")
    right_key, _right_headers = issue_key(client, label="right-key")
    _bind(client, left_seat["seat_id"], left_key)
    _bind(client, right_seat["seat_id"], right_key)
    _left_manager, left_headers = _manager(client, left["tenant_id"])
    _right_manager, right_headers = _manager(client, right["tenant_id"])
    store = UsageStore(db_path=home / "keys.db")
    _record(store, left_key, created_at=1_763_200_000.0, cost=6.0, role="sql", total=11)
    _record(store, left_key, created_at=1_763_300_000.0, cost=4.0, role="tool", total=9)
    _record(store, right_key, created_at=1_763_250_000.0, cost=20.0, role="reason", total=40)

    left_meter = client.get(
        "/api/system/metering",
        params={"account_id": left_account["id"]},
        headers=left_headers,
    )
    assert left_meter.status_code == 200
    body = left_meter.json()
    assert body["ledger_scope"] == "tenant"
    assert body["tenant_id"] == left["tenant_id"]
    assert body["ledger"]["requests"] == 2
    assert body["ledger"]["total_tokens"] == 20
    assert body["ledger"]["est_cost_usd"] == pytest.approx(10.0)
    denied = client.get(
        "/api/system/metering",
        params={"account_id": right_account["id"]},
        headers=left_headers,
    )
    assert denied.status_code == 404
    process = client.get("/api/system/metering", params={"account_id": left_account["id"]})
    assert process.json()["ledger_scope"] == "process"
    assert process.json()["ledger"]["requests"] == 3

    window = client.get(
        "/api/usage",
        params={"since": 1_763_250_000.0, "until": 1_763_280_000.0},
        headers=left_headers,
    )
    assert window.status_code == 200
    page = window.json()
    assert page["count"] == 0
    assert page["totals"]["tenant"]["requests"] == 0
    wide = client.get(
        "/api/usage",
        params={"since": 1_763_100_000.0, "until": 1_763_400_000.0, "limit": 1},
        headers=left_headers,
    ).json()
    assert wide["count"] == 1
    assert wide["totals"]["tenant"]["requests"] == 2
    assert wide["totals"]["tenant"]["est_cost_usd"] == pytest.approx(10.0)
    assert wide["events"][0]["route_role"] == "sql"
    assert wide["events"][0]["est_cost_usd"] == pytest.approx(6.0)
    assert wide["events"][0]["api_key_id"] == left_key
    assert wide["next_cursor"]
    rest = client.get(
        "/api/usage",
        params={"since": 1_763_100_000.0, "until": 1_763_400_000.0, "cursor": wide["next_cursor"]},
        headers=left_headers,
    ).json()
    assert rest["count"] == 1
    assert rest["events"][0]["route_role"] == "tool"
    assert rest["next_cursor"] is None
    other = client.get("/api/usage", headers=right_headers).json()
    assert other["totals"]["tenant"]["requests"] == 1
    assert all(row["api_key_id"] == right_key for row in other["events"])


def test_caps_audit_allowance_and_bad_policy(
    client: TestClient,
    home: Path,
    vault: KeyVault,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OV_SEAT_PLANE", "1")
    _healthy(vault)
    account = _account(client, "billco")
    tenant = _tenant(client, account["id"], "Bill")
    seat = _seat(client, tenant["tenant_id"], "s1")
    key_id, _key_headers = issue_key(client, label="bill-key")
    _bind(client, seat["seat_id"], key_id)
    manager_id, manager_headers = _manager(client, tenant["tenant_id"])
    raised = client.post(
        f"/api/system/tenant/seats/{seat['seat_id']}/caps",
        json={"allowance_usd": 4, "ceiling_usd": 50, "daily_usd": 3},
        headers=manager_headers,
    )
    assert raised.status_code == 200, raised.text
    lowered = client.post(
        f"/api/system/tenant/seats/{seat['seat_id']}/caps",
        json={"daily_usd": 1},
        headers=manager_headers,
    )
    assert lowered.status_code == 200
    markup = client.post(
        "/api/system/tenant/markup",
        json={"tenant_id": tenant["tenant_id"], "markup": 1.5},
        headers=manager_headers,
    )
    assert markup.status_code == 200
    audit = client.get(
        "/api/system/tenant/audit",
        params={"tenant_id": tenant["tenant_id"]},
        headers=manager_headers,
    ).json()["audit"]
    caps = [row for row in audit if row["action"] == "seat_caps"]
    assert caps[0]["actor"] == manager_id
    assert caps[0]["old"]["daily_usd"] == 3
    assert caps[0]["new"]["daily_usd"] == 1
    assert caps[0]["new"]["allowance_usd"] == 4
    assert "token" not in json.dumps(caps)
    assert "ovm_" not in json.dumps(caps)
    store = UsageStore(db_path=home / "keys.db")
    _record(store, key_id, created_at=1_794_744_000.0, cost=10.0, total=5)
    bill = client.get(
        "/api/system/tenant/billing",
        params={"tenant_id": tenant["tenant_id"], "month": "2026-11"},
        headers=manager_headers,
    )
    assert bill.status_code == 200, bill.text
    seat_row = bill.json()["seats"][0]
    assert seat_row["metered_usd"] == pytest.approx(10.0)
    assert seat_row["overage_usd"] == pytest.approx(9.0)
    csv_body = client.get(
        "/api/system/tenant/billing",
        params={"tenant_id": tenant["tenant_id"], "month": "2026-11", "format": "csv"},
        headers=manager_headers,
    )
    assert csv_body.headers["content-type"].startswith("text/csv")
    assert "overage_usd" in csv_body.text
    assert "9.0" in csv_body.text
    other = _account(client, "otherco")
    other_tenant = _tenant(client, other["id"], "Other")
    _other_id, other_headers = _manager(client, other_tenant["tenant_id"])
    hidden = client.get(
        "/api/system/tenant/billing",
        params={"tenant_id": tenant["tenant_id"], "month": "2026-11"},
        headers=other_headers,
    )
    assert hidden.status_code == 404

    # DB ceiling is 50, so the file does not lower that seat. A fresh seat uses the file.
    file_seat = _seat(client, tenant["tenant_id"], "file-seat")
    file_key, file_headers = issue_key(client, label="file-key")
    _bind(client, file_seat["seat_id"], file_key)
    policy = tmp_path / "seat-policy.json"
    policy.write_text(
        json.dumps({"seats": {file_seat["seat_id"]: {"ceiling_usd": 0}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OV_SEAT_POLICY", str(policy))
    reset_seat_policy_cache()
    mock = _mock_ok()
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        blocked = client.post("/v1/chat/completions", json=_CHAT, headers=file_headers)
    assert blocked.status_code == 402
    assert blocked.json()["error"]["type"] == "openvault_seat_ceiling"
    assert mock.post.await_count == 0
    policy.write_text("{", encoding="utf-8")
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        still = client.post("/v1/chat/completions", json=_CHAT, headers=file_headers)
    assert still.status_code == 402
    assert still.json()["error"]["type"] == "openvault_seat_ceiling"
    status = client.get("/api/system/tenant/policy").json()
    assert status["using"] == "last_good"
    assert status["policy_error"]
    assert "seat-policy.json" in status["policy_error"]
    assert str(tmp_path) not in status["policy_error"]
    # A request that is not seated still completes. A bad file is not a 503.
    free_key, free_headers = issue_key(client, label="unseated")
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        ok = client.post("/v1/chat/completions", json=_CHAT, headers=free_headers)
    assert ok.status_code == 200
    assert free_key


def test_allowlist_capture_and_remote_manager(
    client: TestClient, vault: KeyVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OV_SEAT_PLANE", "1")
    _healthy(vault)
    account = _account(client, "capco")
    tenant = _tenant(client, account["id"], "Cap")
    seat = _seat(client, tenant["tenant_id"], "cap-seat")
    key_id, key_headers = issue_key(client, label="cap-key")
    _bind(client, seat["seat_id"], key_id)
    _manager_id, manager_headers = _manager(client, tenant["tenant_id"])
    allow = client.put(
        f"/api/system/tenant/seats/{seat['seat_id']}/allowlist",
        json={"models": ["some-other-model"], "roles": ["sql"]},
        headers=manager_headers,
    )
    assert allow.status_code == 200, allow.text
    mock = _mock_ok()
    ask = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "what is my allowance"}],
    }
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        refused = client.post("/v1/chat/completions", json=ask, headers=key_headers)
    assert refused.status_code == 403
    assert refused.json()["error"]["type"] == "openvault_seat_allowlist"
    assert mock.post.await_count == 0
    client.put(
        f"/api/system/tenant/seats/{seat['seat_id']}/allowlist",
        json={"models": [], "roles": []},
        headers=manager_headers,
    )
    quiet = client.get(
        "/api/system/tenant/questions",
        params={"tenant_id": tenant["tenant_id"]},
        headers=manager_headers,
    ).json()
    assert quiet["enabled"] is False
    assert quiet["questions"] == []
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        sent = client.post("/v1/chat/completions", json=ask, headers=key_headers)
    assert sent.status_code == 200
    still_off = client.get(
        "/api/system/tenant/questions",
        params={"tenant_id": tenant["tenant_id"]},
        headers=manager_headers,
    ).json()
    assert still_off["questions"] == []
    turned = client.post(
        "/api/system/tenant/capture",
        json={"tenant_id": tenant["tenant_id"], "enabled": True, "retention_days": 7},
        headers=manager_headers,
    )
    assert turned.status_code == 200
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        captured = client.post("/v1/chat/completions", json=ask, headers=key_headers)
    assert captured.status_code == 200
    seen = client.get(
        "/api/system/tenant/questions",
        params={"tenant_id": tenant["tenant_id"]},
        headers=manager_headers,
    ).json()
    assert seen["questions"][0]["text"] == "what is my allowance"
    assert seen["questions"][0]["seat_id"] == seat["seat_id"]
    assert (
        client.get(
            "/api/system/tenant/questions",
            params={"tenant_id": tenant["tenant_id"]},
        ).status_code
        == 404
    )
    other_account = _account(client, "peekco")
    other = _tenant(client, other_account["id"], "Peek")
    _peek_id, peek_headers = _manager(client, other["tenant_id"])
    assert (
        client.get(
            "/api/system/tenant/questions",
            params={"tenant_id": tenant["tenant_id"]},
            headers=peek_headers,
        ).status_code
        == 404
    )
    assert key_id

    app = client.app
    remote = TestClient(app, client=("203.0.113.10", 5555))
    scoped = remote.get(
        "/api/system/metering",
        params={"account_id": account["id"]},
        headers=manager_headers,
    )
    assert scoped.status_code == 200
    assert scoped.json()["ledger_scope"] == "tenant"
    assert (
        remote.get(
            "/api/system/metering",
            params={"account_id": account["id"]},
        ).status_code
        == 401
    )
    assert remote.get("/api/providers/catalog", headers=manager_headers).status_code == 401
    monkeypatch.setenv("OV_SEAT_PLANE", "0")
    assert (
        remote.get(
            "/api/system/metering",
            params={"account_id": account["id"]},
            headers=manager_headers,
        ).status_code
        == 401
    )


def test_routes_are_404_when_the_flag_is_off(client: TestClient) -> None:
    account = _account(client, "offco")
    missing = client.post(
        "/api/system/tenant/tenants",
        json={"account_id": account["id"], "label": "Off"},
    )
    assert missing.status_code == 404
