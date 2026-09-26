"""POST /api/apikeys/verify: the check FreeRoute calls instead of keeping keys (DR-0018)."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


@pytest.fixture()
def app_and_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.setenv("OPENVAULT_KEY", Fernet.generate_key().decode())
    vault = KeyVault(db_path=root / "keys.db", seal=Seal(Fernet.generate_key()))
    return create_app(vault=vault, mock_health=True, enable_precheck_loop=False)


def test_valid_token_verifies_and_revoked_does_not(app_and_home: FastAPI) -> None:
    client = TestClient(app_and_home, client=("127.0.0.1", 5555))
    minted = client.post("/api/apikeys", json={"label": "freeroute-client", "tier": "free"}).json()
    token, key_id = minted["token"], minted["key"]["key_id"]

    ok = client.post("/api/apikeys/verify", json={"token": token})
    assert ok.status_code == 200
    assert ok.json() == {"valid": True, "key_id": key_id, "tier": "free"}

    assert client.post("/api/apikeys/verify", json={"token": "ov_nope"}).json() == {"valid": False}

    client.delete(f"/api/apikeys/{key_id}")
    assert client.post("/api/apikeys/verify", json={"token": token}).json() == {"valid": False}


def test_verify_refuses_non_loopback_peer(app_and_home: FastAPI) -> None:
    local = TestClient(app_and_home, client=("127.0.0.1", 5555))
    token = local.post("/api/apikeys", json={"label": "x", "tier": "free"}).json()["token"]

    remote = TestClient(app_and_home, client=("203.0.113.7", 5555))
    res = remote.post(
        "/api/apikeys/verify",
        json={"token": token},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
