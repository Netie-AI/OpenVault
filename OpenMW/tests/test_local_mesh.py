"""Local mesh — OpenVault ↔ Cortex ↔ FreeIDE handshake + connect pack."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    home = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(home))
    monkeypatch.setenv("CORTEX_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENIDE_URL", "http://127.0.0.1:5100")
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=home / "keys.db", seal=seal)
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:8000",
        openide_url="http://127.0.0.1:5100",
    )
    return TestClient(app)


def test_mesh_status_and_connect_pack(client: TestClient) -> None:
    res = client.get("/api/local/mesh")
    assert res.status_code == 200
    body = res.json()
    assert "openvault" in body["mesh"]["peers"]
    assert "cortex" in body["mesh"]["peers"]
    assert "openide" in body["mesh"]["peers"]
    pack = body["connect_pack"]
    assert pack["schema"] == "openvault.local.connect_pack/v1"
    assert pack["openvault"]["v1"].endswith("/v1")
    assert "CORTEX_URL" in pack["env"]
    assert pack["openvault"]["crew_gate"].endswith("/api/crew/gate")
    assert pack["cortex"]["skills"].endswith("/api/skills")


def test_handshake_auto_approve_openide_and_invoke(client: TestClient) -> None:
    hs = client.post(
        "/api/local/handshake",
        json={
            "peer_kind": "openide",
            "name": "FreeIDE test",
            "base_url": "http://127.0.0.1:5100",
            "capabilities": ["signin", "passkey"],
            "auto_approve": True,
        },
    )
    assert hs.status_code == 200
    assert hs.json()["handshake"]["status"] == "approved"
    assert hs.json()["peer"]["approved"] is True

    cortex = client.post(
        "/api/local/handshake",
        json={
            "peer_kind": "cortex",
            "name": "Cortex test",
            "base_url": "http://127.0.0.1:8000",
            "capabilities": ["engines"],
            "auto_approve": True,
        },
    )
    assert cortex.status_code == 200
    assert cortex.json()["handshake"]["status"] == "approved"

    inv = client.post(
        "/api/freeide/invoke",
        json={"action": "complete_signin", "username": "acmeops"},
    )
    assert inv.status_code == 200
    assert inv.json()["ok"] is True
    assert "urls" in inv.json()

    pack = client.get("/api/local/connect-pack")
    assert pack.status_code == 200
    assert pack.json()["openide"]["approved"] is True
    assert pack.json()["cortex"]["approved"] is True


def test_handshake_decide_reject(client: TestClient) -> None:
    hs = client.post(
        "/api/local/handshake",
        json={
            "peer_kind": "airgpt",
            "name": "AirGPT",
            "base_url": "http://10.0.0.5:9000",
            "auto_approve": False,
        },
    )
    assert hs.status_code == 200
    req_id = hs.json()["handshake"]["request_id"]
    assert hs.json()["handshake"]["status"] == "pending"

    decided = client.post(
        f"/api/local/handshake/{req_id}/decide",
        json={"approve": False, "note": "not local"},
    )
    assert decided.status_code == 200
    assert decided.json()["handshake"]["status"] == "rejected"


def test_mesh_config_update(client: TestClient) -> None:
    res = client.put(
        "/api/local/mesh/config",
        json={
            "auto_approve_loopback": True,
            "cortex_url": "http://127.0.0.1:8001",
            "openide_url": "http://127.0.0.1:5101",
        },
    )
    assert res.status_code == 200
    peers = res.json()["mesh"]["peers"]
    assert peers["cortex"]["base_url"] == "http://127.0.0.1:8001"
    assert peers["openide"]["base_url"] == "http://127.0.0.1:5101"
