"""Access-gate decisions that must survive unhooking mesh from route/access.py.

These are the live custody decisions. Mesh peer status must not move them.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import inject_admin_credential, issue_key
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.paths import ensure_home
from openmw.openvault.route.access import (
    AccessIntent,
    AccessKind,
    ResolveResult,
    resolve_access,
)
from openmw.openvault.vault.admin_token import ADMIN_HEADER, ensure_admin_token
from openmw.openvault.vault.store import KeyVault

INTENT = {"X-OpenVault-Reveal": "intentional"}
_PEER = "10.128.0.3"
_REMOTE = "203.0.113.10"
_NON_MESH: tuple[tuple[AccessKind, str, AccessIntent], ...] = (
    ("service", "service.freeroute", "read"),
    ("service", "service.freebuild", "deploy"),
    ("model", "model.slots", "read"),
    ("skill", "netie-kb.skills", "invoke"),
    ("mcp", "netie-kb.mcp", "invoke"),
    ("runtime", "runtime.local", "read"),
    ("api", "provider.groq", "read"),
)


@pytest.fixture(autouse=True)
def _do_not_inject_admin() -> Any:
    token = inject_admin_credential.set(False)
    yield
    inject_admin_credential.reset(token)


@pytest.fixture()
def home(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.delenv("OPENVAULT_ADMIN_TOKEN_PATH", raising=False)
    monkeypatch.delenv("OPENVAULT_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("OPENVAULT_SERVICES_ALLOW", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    monkeypatch.delenv("NETIE_KB_URL", raising=False)
    return root


@pytest.fixture()
def app(home: Any) -> FastAPI:
    return create_app(
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )


def _client(app: FastAPI, host: str) -> TestClient:
    return TestClient(app, client=(host, 5555))


def _decision(result: ResolveResult) -> tuple[Any, ...]:
    return (
        result.found,
        result.allowed,
        tuple(result.reasons),
        result.location,
        result.gate.get("action"),
        result.gate.get("allowed"),
    )


def test_access_module_does_not_import_mesh() -> None:
    import openmw.openvault.route.access as access

    tree = ast.parse(Path(access.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "mesh" in node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names if "mesh" in alias.name)
    assert imported == []


def test_non_mesh_decisions_ignore_an_offline_mesh_peer(home: Any) -> None:
    """Offline cortex changes the mesh memory verdict only."""
    vault = KeyVault()
    before = {
        item: _decision(
            resolve_access(vault=vault, kind=item[0], resource_id=item[1], intent=item[2])
        )
        for item in _NON_MESH
    }
    memory_before = resolve_access(
        vault=vault, kind="memory", resource_id="cortex.memory", intent="read"
    )
    assert memory_before.allowed is True

    assert Path(home).resolve() == ensure_home()
    path = ensure_home() / "local_mesh.json"
    path.write_text(
        json.dumps(
            {
                "peers": {
                    "cortex": {
                        "kind": "cortex",
                        "name": "Cortex / Netie Engine",
                        "base_url": "http://127.0.0.1:8010",
                        "status": "offline",
                        "detail": "down",
                        "last_seen": None,
                        "approved": False,
                        "meta": {},
                    }
                },
                "handshakes": {},
                "auto_approve_loopback": True,
                "updated_at": 1.0,
            }
        ),
        encoding="utf-8",
    )

    after = {
        item: _decision(
            resolve_access(vault=vault, kind=item[0], resource_id=item[1], intent=item[2])
        )
        for item in _NON_MESH
    }
    assert after == before

    memory_after = resolve_access(
        vault=vault, kind="memory", resource_id="cortex.memory", intent="read"
    )
    assert memory_after.allowed is False
    assert any("offline" in reason for reason in memory_after.reasons)
    assert memory_after.location.endswith("/api/memory")


def test_access_gate_http_decisions_unchanged(app: FastAPI) -> None:
    """loopback admin, service Bearer, dms intermediate, mint, re-register, revoke."""
    loop = _client(app, "127.0.0.1")
    admin = ensure_admin_token()

    missing_admin = loop.get("/api/keys")
    assert missing_admin.status_code == 401
    assert missing_admin.json()["error"]["type"] == "openvault_unauthenticated"

    with_admin = loop.get("/api/keys", headers={ADMIN_HEADER: admin})
    assert with_admin.status_code == 200

    _key_id, service_bearer = issue_key(loop)
    remote = _client(app, _REMOTE)
    no_bearer = remote.get("/api/freeroute/status")
    assert no_bearer.status_code == 401
    assert no_bearer.json()["error"]["type"] == "openvault_unauthenticated"
    with_bearer = remote.get("/api/freeroute/status", headers=service_bearer)
    assert with_bearer.status_code == 200

    peer = _client(app, _PEER)
    first = peer.post("/keys/services", json={"service_id": "dms"}, headers=INTENT)
    assert first.status_code == 200
    assert first.json()["service_id"] == "dms"
    dms_token = str(first.json()["token"])
    if not dms_token:
        raise AssertionError("dms service mint returned an empty token")

    again = peer.post("/keys/services", json={"service_id": "dms"}, headers=INTENT)
    assert again.status_code == 401
    assert "token" not in again.json()

    issued = loop.post(
        "/keys/intermediate",
        json={"service_id": "dms", "subject": "dms-manifest-signer", "ttl_s": 300},
        headers={"Authorization": f"Bearer {dms_token}"},
    )
    assert issued.status_code == 200
    kid = str(issued.json()["kid"])
    assert kid.startswith("int-")

    bare = loop.post(f"/keys/intermediate/{kid}/revoke")
    assert bare.status_code == 401
    assert bare.json().get("lifecycle") != "revoked"
    listed = [str(item["kid"]) for item in loop.get("/keys/jwks").json()["keys"]]
    assert kid in listed
