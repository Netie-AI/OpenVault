"""Loopback app grant: other local app asks, human grants, token once."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.setenv("OPENVAULT_KEY", Fernet.generate_key().decode())
    return root


@pytest.fixture()
def client(home: Path) -> TestClient:
    vault = KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    return TestClient(app, client=("127.0.0.1", 5555))


def test_grant_flow_delivers_token_once(client: TestClient, home: Path) -> None:
    started = client.post("/api/local/grants", json={"client_name": "MyApp"}).json()
    assert started["status"] == "pending"
    assert started["user_code"]
    grant_id = started["grant_id"]
    pending = client.post(f"/api/local/grants/{grant_id}/poll")
    assert pending.status_code == 202
    decided = client.post(f"/api/local/grants/{grant_id}/decide", json={"approve": True})
    assert decided.status_code == 200, decided.text
    first = client.post(f"/api/local/grants/{grant_id}/poll")
    assert first.status_code == 200
    token = first.json()["token"]
    assert token.startswith("ov_")
    second = client.post(f"/api/local/grants/{grant_id}/poll")
    assert second.json()["token"] is None
    disk = (home / "app_grants.json").read_text(encoding="utf-8")
    assert "ov_" not in disk


def test_grant_deny_is_403_on_poll(client: TestClient) -> None:
    started = client.post("/api/local/grants", json={"client_name": "Nope"}).json()
    grant_id = started["grant_id"]
    client.post(f"/api/local/grants/{grant_id}/decide", json={"approve": False})
    poll = client.post(f"/api/local/grants/{grant_id}/poll")
    assert poll.status_code == 403


def test_grant_is_loopback_only(home: Path) -> None:
    vault = KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    remote = TestClient(app, client=("192.168.1.50", 5555))
    res = remote.post("/api/local/grants", json={"client_name": "Lan"})
    assert res.status_code == 403
