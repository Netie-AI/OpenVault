"""SYSTEM control plane: entitlements, routing, unlock, metering, seats (#48).

Locks the founder display SKUs and refuses to invent usage $/unit. The HTTP
surface is loopback-only and is not a public rate page. No public :5000 bind.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.accounts import AccountStore
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.system_plane import (
    CUSTODY_PORT,
    DEFAULT_BIND_HOST,
    INTERNAL_WRITERS_URL,
    PLANS,
    SEAT_USD,
    ULTRA_GIGA_PLAN_IDS,
    USAGE_CREDIT_DISCOUNT_PCT,
    EntitlementStore,
    SystemPlaneError,
    catalog_payload,
    monthly_display_usd,
    require_private_bind,
    usage_credit_factor,
)
from openmw.openvault.vault.usage_store import USAGE_UNIT_STATUS, USAGE_UNIT_USD, UsageStore

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.delenv("OPENVAULT_ALLOW_PUBLIC_BIND", raising=False)
    return root


@pytest.fixture()
def client(home: Path) -> TestClient:
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=home / "keys.db", seal=seal)
    accounts = AccountStore(db_path=home / "accounts.db")
    app = create_app(
        vault=vault,
        accounts=accounts,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def _account(client: TestClient, name: str = "Acme") -> dict[str, Any]:
    res = client.post(
        "/api/accounts",
        json={"display_name": name, "auth_provider": "netie_email", "local_part": name.lower()},
    )
    assert res.status_code == 200, res.text
    payload: dict[str, Any] = res.json()
    return payload


def test_locked_display_skus_are_the_founder_numbers() -> None:
    assert PLANS["basic"].display_usd == 10
    assert PLANS["pro"].display_usd == 100
    assert PLANS["ultra"].display_usd == 500
    assert PLANS["team"].display_usd == 10
    assert PLANS["team_ultra"].display_usd == 500
    assert PLANS["team_giga"].display_usd == 1000
    assert SEAT_USD == 30
    catalog = catalog_payload()
    assert catalog["seat_usd"] == 30
    assert catalog["plans"]["basic"]["display_usd"] == 10
    assert catalog["plans"]["team_giga"]["display_usd"] == 1000
    assert catalog["public_rate_page"] is False
    assert catalog["marketing_owner"] == "netie.ai"


def test_usage_unit_stays_needs_you() -> None:
    """A number here would be indistinguishable from a measured rate."""
    assert USAGE_UNIT_USD is None
    assert USAGE_UNIT_STATUS == "NEEDS-YOU"
    usage = catalog_payload()["usage"]
    assert usage["unit_usd"] is None
    assert usage["unit_status"] == "NEEDS-YOU"
    assert usage["priced"] is False
    assert usage["credit_discount_pct_ultra_giga"] == 20
    assert usage["credit_normal_on"] == ["pro", "seat"]


def test_usage_credits_are_20_percent_on_ultra_giga_only() -> None:
    assert USAGE_CREDIT_DISCOUNT_PCT == 20
    assert {"ultra", "team_ultra", "team_giga"} == ULTRA_GIGA_PLAN_IDS
    assert usage_credit_factor("ultra") == 0.8
    assert usage_credit_factor("team_ultra") == 0.8
    assert usage_credit_factor("team_giga") == 0.8
    assert usage_credit_factor("pro") == 1.0
    assert usage_credit_factor("basic") == 1.0
    assert usage_credit_factor("team") == 1.0
    assert usage_credit_factor("") == 1.0


def test_team_monthly_display_adds_seat_usd_not_usage() -> None:
    assert monthly_display_usd("pro", 99) == 100
    assert monthly_display_usd("team", 1) == 10 + 30
    assert monthly_display_usd("team_giga", 3) == 1000 + 90
    assert monthly_display_usd("", 5) is None


def test_catalog_and_bind_are_loopback_only(client: TestClient) -> None:
    ok = client.get("/api/system/catalog")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["bind"]["internal_writers_url"] == INTERNAL_WRITERS_URL
    assert body["bind"]["default_host"] == DEFAULT_BIND_HOST
    assert body["bind"]["custody_port"] == CUSTODY_PORT
    assert body["bind"]["public_bind_allowed"] is False
    assert body["bind"]["internal_writers_only"] is True
    assert "35.253.229.206:8080" in body["bind"]["internal_writers_url"]
    assert client.get("/api/system/bind").json()["internal_writers_only"] is True

    remote = TestClient(client.app, client=("8.8.8.8", 5555))
    assert remote.get("/api/system/catalog").status_code == 403
    assert remote.get("/api/system/bind").status_code == 403
    denied = remote.post("/api/system/unlock", json={"account_id": "x", "plan_id": "pro"})
    assert denied.status_code == 403


def test_unlock_route_metering_and_seats(client: TestClient) -> None:
    acct = _account(client)
    account_id = acct["id"]

    locked = client.get(f"/api/system/entitlements/{account_id}")
    assert locked.status_code == 200
    assert locked.json()["entitlement"]["unlocked"] is False
    route = client.get("/api/system/route", params={"account_id": account_id}).json()
    assert route["allowed"] is False
    assert route["limiter_tier"] is None
    assert route["reason"] == "locked"

    unlocked = client.post(
        "/api/system/unlock", json={"account_id": account_id, "plan_id": "ultra"}
    )
    assert unlocked.status_code == 200, unlocked.text
    ent = unlocked.json()["entitlement"]
    assert ent["plan_id"] == "ultra"
    assert ent["display_usd"] == 500
    assert ent["seats"] == 0
    assert ent["usage_credit_factor"] == 0.8
    assert ent["limiter_tier"] == "pro"
    assert ent["monthly_display_usd"] == 500

    routed = client.get("/api/system/route", params={"account_id": account_id}).json()
    assert routed["allowed"] is True
    assert routed["limiter_tier"] == "pro"
    assert routed["intents"] == ["connect", "invoke"]

    meter = client.get("/api/system/metering", params={"account_id": account_id}).json()
    assert meter["priced"] is False
    assert meter["usage_unit_usd"] is None
    assert meter["usage_unit_status"] == "NEEDS-YOU"
    assert meter["usage_credit_factor"] == 0.8
    assert meter["ledger_scope"] == "process"
    assert meter["ledger"]["priced"] is False
    assert meter["ledger"]["usage_unit_usd"] is None

    bundle = client.get(f"/api/accounts/{account_id}").json()
    assert bundle["entitlement"]["plan_id"] == "ultra"

    seats = client.post("/api/system/seats", json={"account_id": account_id, "seats": 2})
    assert seats.status_code == 400
    assert "team" in seats.json()["detail"]

    with_seats = client.post(
        "/api/system/unlock",
        json={"account_id": account_id, "plan_id": "basic", "seats": 2},
    )
    assert with_seats.status_code == 400

    team = client.post(
        "/api/system/unlock",
        json={"account_id": account_id, "plan_id": "team_giga", "seats": 2},
    )
    assert team.status_code == 200, team.text
    tent = team.json()["entitlement"]
    assert tent["seats"] == 2
    assert tent["monthly_display_usd"] == 1000 + 60
    assert tent["usage_credit_factor"] == 0.8
    assert tent["limiter_tier"] == "pro"

    three = client.post("/api/system/seats", json={"account_id": account_id, "seats": 3})
    assert three.status_code == 200
    assert three.json()["entitlement"]["monthly_display_usd"] == 1000 + 90

    pro = client.post("/api/system/unlock", json={"account_id": account_id, "plan_id": "pro"})
    assert pro.status_code == 200
    pent = pro.json()["entitlement"]
    assert pent["seats"] == 0
    assert pent["usage_credit_factor"] == 1.0
    assert pent["monthly_display_usd"] == 100

    locked_again = client.post("/api/system/lock", json={"account_id": account_id})
    assert locked_again.status_code == 200
    assert locked_again.json()["entitlement"]["unlocked"] is False


def test_unknown_plan_and_missing_account(client: TestClient) -> None:
    acct = _account(client, "Solo")
    bad = client.post(
        "/api/system/unlock", json={"account_id": acct["id"], "plan_id": "enterprise"}
    )
    assert bad.status_code == 400
    assert "unknown plan" in bad.json()["detail"]
    missing = client.post("/api/system/unlock", json={"account_id": "nope", "plan_id": "pro"})
    assert missing.status_code == 404
    assert client.get("/api/system/entitlements/nope").status_code == 404
    assert client.get("/api/system/route", params={"account_id": "nope"}).status_code == 404
    assert client.get("/api/system/metering", params={"account_id": "nope"}).status_code == 404
    assert client.post("/api/system/lock", json={"account_id": "nope"}).status_code == 404
    seats_missing = client.post("/api/system/seats", json={"account_id": "nope", "seats": 2})
    assert seats_missing.status_code == 404


def test_entitlements_share_accounts_db_not_a_second_vault(home: Path) -> None:
    accounts = AccountStore(db_path=home / "accounts.db")
    store = EntitlementStore(db_path=accounts.db_path)
    created = accounts.create(display_name="One", auth_provider="netie_email", local_part="one")
    store.unlock(created.id, "team", seats=4, accounts=accounts)
    kept = store.unlock(created.id, "team_ultra", accounts=accounts)
    assert kept.seats == 4
    assert kept.plan_id == "team_ultra"
    assert store.db_path == accounts.db_path
    assert store.db_path == home / "accounts.db"
    loaded = EntitlementStore(db_path=home / "accounts.db")
    row = loaded.get(created.id)
    assert row.seats == 4
    assert row.plan_id == "team_ultra"
    assert usage_credit_factor(row.plan_id) == 0.8


def test_require_private_bind_blocks_public_5000(monkeypatch: pytest.MonkeyPatch) -> None:
    assert require_private_bind("127.0.0.1") == "127.0.0.1"
    with pytest.raises(SystemPlaneError, match="public :5000"):
        require_private_bind("0.0.0.0")
    with pytest.raises(SystemPlaneError, match=r"35\.253\.229\.206:8080"):
        require_private_bind("::")
    monkeypatch.setenv("OPENVAULT_ALLOW_PUBLIC_BIND", "1")
    assert require_private_bind("0.0.0.0") == "0.0.0.0"


def test_launchers_keep_loopback_bind() -> None:
    cli = (REPO_ROOT / "OpenMW" / "openmw" / "cli.py").read_text(encoding="utf-8")
    assert 'typer.Option("127.0.0.1", "--host"' in cli
    launcher = (REPO_ROOT / "apps" / "cli" / "openvault_cli.py").read_text(encoding="utf-8")
    assert '"127.0.0.1"' in launcher
    assert "--host" in launcher
    demo = (REPO_ROOT / "OpenMW" / "openmw" / "cli.py").read_text(encoding="utf-8")
    assert '"127.0.0.1"' in demo


def test_usage_ledger_summary_stays_unpriced(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    store = UsageStore(db_path=home / "keys.db")
    summary = store.summary()
    assert summary["priced"] is False
    assert summary["usage_unit_usd"] is None
    assert summary["usage_unit_status"] == "NEEDS-YOU"
