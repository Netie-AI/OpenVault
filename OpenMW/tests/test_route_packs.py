"""Experience packs: $10 starter (~$8 credit), simulate only, 402 when spent."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.route_packs import (
    PACKS,
    estimated_usd,
    evaluate_pack,
)
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.usage_store import UsageEvent, UsageStore


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.setenv("OPENVAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("OPENVAULT_BLEND_USD_PER_1M", raising=False)
    return root


@pytest.fixture()
def client(home: Path) -> TestClient:
    vault = KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    return TestClient(app, client=("127.0.0.1", 5555))


def test_starter_is_ten_dollars_and_eight_credit() -> None:
    starter = PACKS["starter"]
    assert starter.price_usd == 10.0
    assert starter.api_credit_usd == 8.0
    assert PACKS["plus"].price_usd == 30.0
    assert PACKS["pro"].price_usd == 100.0
    assert PACKS["studio"].price_usd == 500.0


def test_catalog_endpoint_lists_packs(client: TestClient) -> None:
    res = client.get("/api/keys/packs")
    assert res.status_code == 200
    body = res.json()
    ids = {p["id"] for p in body["packs"]}
    assert ids == {"starter", "plus", "pro", "studio"}
    assert body["stripe_mode"] == "simulate"
    assert any("Register" in step for step in body["stuck_next_steps"])


def test_no_pack_does_not_block_issued_key(client: TestClient) -> None:
    minted = client.post("/api/apikeys", json={"label": "app", "tier": "free"}).json()
    key_id = minted["key"]["key_id"]
    gate = evaluate_pack(key_id, billable_tokens=50_000_000)
    assert gate["allowed"] is True
    assert gate["pack"] is None


def test_exhausted_pack_is_402(client: TestClient, home: Path) -> None:
    minted = client.post(
        "/api/apikeys",
        json={"label": "app", "tier": "free", "pack_id": "starter"},
    ).json()
    assert minted["pack"]["credit_usd"] == 8.0
    token = minted["token"]
    key_id = minted["key"]["key_id"]
    # Default blend 0.20 USD / 1M tokens: 50M tokens => $10 > $8 credit.
    UsageStore(db_path=home / "keys.db").record(
        UsageEvent(
            identity=key_id,
            tier="free",
            api_key_id=key_id,
            total_tokens=50_000_000,
            status=200,
        )
    )
    gate = evaluate_pack(key_id, billable_tokens=50_000_000)
    assert gate["allowed"] is False
    assert gate["error_type"] == "openvault_pack_exhausted"
    chat = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert chat.status_code == 402
    err = chat.json()["error"]
    assert err["type"] == "openvault_pack_exhausted"
    assert err["next_steps"]


def test_unknown_pack_id_is_400(client: TestClient) -> None:
    res = client.post(
        "/api/apikeys",
        json={"label": "app", "tier": "free", "pack_id": "enterprise"},
    )
    assert res.status_code == 400
    assert "unknown pack_id" in res.text


def test_simulate_attaches_pack_to_existing_key(client: TestClient) -> None:
    minted = client.post("/api/apikeys", json={"label": "app", "tier": "free"}).json()
    key_id = minted["key"]["key_id"]
    res = client.post(
        "/api/keys/packs/simulate",
        json={"api_key_id": key_id, "pack_id": "plus"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["pack"]["credit_usd"] == 24.0
    assert res.json()["pack"]["pack_id"] == "plus"


def test_estimated_usd_is_labeled_math_not_a_price_table() -> None:
    assert estimated_usd(1_000_000, usd_per_1m=0.20) == 0.2
    assert estimated_usd(0) == 0.0
