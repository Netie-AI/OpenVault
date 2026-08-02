"""Layer-contract conformance (PRODUCT_ROLES.md + the FreeIDE/OpenVault bridge).

These tests lock the cross-surface contract so it cannot drift silently:
custody/gate/ship stay in OpenVault, the mesh hands out the canonical URLs,
and "skip the rules" flags can never turn into a silent allow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.mesh.local_mesh import DEFAULT_PORTS, OPENIDE_DEFAULT_URL
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault

# Ports cheat-sheet from the contract. FreeIDE is served by AirGPT on :8765;
# :5100 is the legacy standalone stub and must not be the default.
CONTRACT_PORTS = {
    "openvault": 5000,
    "cortex": 8010,
    "openide": 8765,
    "rust_console": 5055,
}

# OpenVault-side routes the bridge and gate contracts depend on.
CONTRACT_ROUTES = [
    ("GET", "/api/healthz"),
    ("GET", "/api/local/mesh"),
    ("PUT", "/api/local/mesh/config"),
    ("GET", "/api/local/connect-pack"),
    ("POST", "/api/local/handshake"),
    ("POST", "/api/freeide/invoke"),
    ("GET", "/api/freeide/ready"),
    ("GET", "/api/keyvault/snapshot"),
    ("POST", "/api/keyvault/upsert"),
    ("POST", "/api/gate/check"),
    ("POST", "/api/cloud/shares"),
    ("POST", "/api/cloud/firewall/check"),
    ("POST", "/api/cloud/sessions"),
    ("POST", "/v1/chat/completions"),
]


@pytest.fixture()
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    home = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(home))
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=home / "keys.db", seal=seal)
    # No openide_url override: we are asserting the *default* is on contract.
    return create_app(vault=vault, mock_health=True, enable_precheck_loop=False)


def _routes(app: FastAPI) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if isinstance(path, str) and methods:
            for method in methods:
                found.add((str(method), path))
    return found


def test_default_ports_match_contract() -> None:
    assert DEFAULT_PORTS == CONTRACT_PORTS
    assert OPENIDE_DEFAULT_URL == "http://127.0.0.1:8765"
    assert "5100" not in OPENIDE_DEFAULT_URL


def test_contract_routes_are_served(app: FastAPI) -> None:
    served = _routes(app)
    missing = [f"{m} {p}" for m, p in CONTRACT_ROUTES if (m, p) not in served]
    assert missing == [], f"contract routes not served: {missing}"


def test_connect_pack_pins_openide_to_airgpt(app: FastAPI) -> None:
    """The connect pack is the shared wiring doc — it must not hand out the stub."""
    with TestClient(app) as client:
        pack = client.get("/api/local/connect-pack").json()
    assert pack["schema"] == "openvault.local.connect_pack/v1"
    assert pack["openide"]["base_url"] == "http://127.0.0.1:8765"
    assert pack["env"]["OPENIDE_URL"] == "http://127.0.0.1:8765"
    # OpenVault stays the custody + gate surface in the pack it publishes.
    assert pack["openvault"]["base_url"] == "http://127.0.0.1:5000"
    assert pack["cortex"]["base_url"] == "http://127.0.0.1:8010"


@pytest.mark.parametrize("flag", ["bypass", "bypass_gate", "force", "skip_rules"])
def test_gate_never_silently_allows_bypass(app: FastAPI, flag: str) -> None:
    with TestClient(app) as client:
        res = client.post("/api/gate/check", json={"action": "deploy", flag: True})
    assert res.status_code == 200
    body = res.json()
    assert body["allowed"] is False, f"{flag} produced a silent allow"
    assert body["reasons"], "a denial must explain itself"


@pytest.mark.parametrize("flag", ["bypass", "force", "skip_rules"])
def test_cloud_firewall_never_honors_bypass(app: FastAPI, flag: str) -> None:
    with TestClient(app) as client:
        res = client.post("/api/cloud/firewall/check", json={"action": "share_lan", flag: True})
    assert res.status_code == 200
    assert res.json()["allowed"] is False


def test_openide_ready_preflights_from_openvault(app: FastAPI) -> None:
    with TestClient(app) as client:
        res = client.get("/api/freeide/ready")
    assert res.status_code == 200
    body = res.json()
    # Keys SoT is OpenVault (AirGPT env.local is only an offline cache).
    assert body["keys_source_of_truth"] == "openvault"
    assert body["openide"]["base_url"] == "http://127.0.0.1:8765"
    assert "gate" in body and "perfect_local" in body
    # Empty vault -> not ready, and it says why.
    assert body["keys_ready"] is False
    assert body["ready"] is False


def test_keyvault_snapshot_declares_openvault_as_source(app: FastAPI) -> None:
    with TestClient(app) as client:
        body = client.get("/api/keyvault/snapshot").json()
    assert body["source"] == "openvault"
    assert body["save_endpoint"] == "/api/keyvault/upsert"
    # FreeRoute is the free-gateway brand OpenVault routes for.
    assert "omniroute" in body["free_fallback_order"]


def test_openfree_status_aliases_for_cortex(app: FastAPI) -> None:
    """Cortex workflow_openvault.check_openfree_budget reads remaining_tokens."""
    client = TestClient(app)
    resp = client.get("/api/freeroute/ratelimit", params={"identity": "wf:test", "tier": "free"})
    assert resp.status_code == 200
    data = resp.json()
    assert "token_remaining" in data
    assert data["remaining_tokens"] == data["token_remaining"]
    assert data["remaining"] == data["token_remaining"]
