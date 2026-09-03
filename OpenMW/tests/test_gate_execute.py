"""Leave-machine execute must call check_gate (OpenVault#22).

Empty or sealed vault -> 403 with gate reasons; no adapter/ship side effect.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault import keywrap
from openmw.openvault.vault.accounts import AccountStore
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault

PASSPHRASE = "correct-horse-battery-staple"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.setenv("OPENSHIP_MODE", "simulate")
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


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "web"
    project.mkdir()
    (project / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    return project


def _seed_key(client: TestClient) -> None:
    created = client.post(
        "/api/keys",
        json={
            "label": "groq",
            "provider": "groq",
            "secret": "gsk-test-gate-execute",
            "role": "free",
        },
    )
    assert created.status_code == 200, created.text


def test_deploy_execute_refuses_empty_vault(client: TestClient, tmp_path: Path) -> None:
    project = _project(tmp_path)
    plan = client.post(
        "/api/deploy/from-cortex",
        json={"project_path": str(project), "subdomain": "app.example.com"},
    )
    assert plan.status_code == 200
    deploy_id = plan.json()["deploy_id"]

    exe = client.post(f"/api/deploy/{deploy_id}/execute", json={"simulate": True})
    assert exe.status_code == 403
    detail = exe.json()["detail"]
    assert detail["allowed"] is False
    assert detail["keys_ready"] is False
    assert detail["reasons"]


def test_freebuild_execute_and_plan_execute_refuse_empty_vault(
    client: TestClient, tmp_path: Path
) -> None:
    project = _project(tmp_path)
    planned = client.post(
        "/api/freebuild/plan",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "execute": False,
        },
    )
    assert planned.status_code == 200
    ship_id = planned.json()["ship_id"]

    exe = client.post(f"/api/freebuild/{ship_id}/execute", json={"simulate": True})
    assert exe.status_code == 403
    assert exe.json()["detail"]["allowed"] is False

    with_exec = client.post(
        "/api/freebuild/plan",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "execute": True,
            "simulate": True,
        },
    )
    assert with_exec.status_code == 403
    assert with_exec.json()["detail"]["allowed"] is False


def test_one_press_auto_execute_refuses_empty_vault(client: TestClient, tmp_path: Path) -> None:
    project = _project(tmp_path)
    res = client.post(
        "/api/deploy/one-press",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "auto_execute": True,
            "simulate": True,
            "target": "local_demo",
        },
    )
    assert res.status_code == 403
    assert res.json()["detail"]["allowed"] is False


def test_one_press_without_auto_execute_skips_leave_gate(
    client: TestClient, tmp_path: Path
) -> None:
    """Planning-only one-press must not invent a second gate block."""
    project = _project(tmp_path)
    res = client.post(
        "/api/deploy/one-press",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "auto_execute": False,
            "simulate": True,
            "target": "local_demo",
        },
    )
    assert res.status_code == 200
    assert res.json()["one_press"]["executed"] is False


def test_deploy_execute_allows_when_gate_open(client: TestClient, tmp_path: Path) -> None:
    _seed_key(client)
    project = _project(tmp_path)
    plan = client.post(
        "/api/deploy/from-cortex",
        json={"project_path": str(project), "subdomain": "app.example.com"},
    )
    assert plan.status_code == 200
    deploy_id = plan.json()["deploy_id"]

    exe = client.post(f"/api/deploy/{deploy_id}/execute", json={"simulate": True})
    assert exe.status_code == 200, exe.text
    assert exe.json()["openship"]["executed"] is True


def test_gate_check_sealed_keys_ready_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    first = TestClient(
        create_app(mock_health=True, enable_precheck_loop=False),
        client=("127.0.0.1", 5555),
    )
    created = first.post(
        "/api/keys",
        json={
            "label": "groq",
            "provider": "groq",
            "secret": "gsk-test-sealed-gate",
            "role": "free",
        },
    )
    assert created.status_code == 200
    assert first.post("/api/vault/passphrase", json={"passphrase": PASSPHRASE}).status_code == 200

    sealed = TestClient(
        create_app(mock_health=True, enable_precheck_loop=False),
        client=("127.0.0.1", 5555),
    )
    assert sealed.get("/api/vault/status").json()["sealed"] is True
    assert keywrap.peek_method((tmp_path / "master.key").read_bytes()) == keywrap.METHOD_PASSPHRASE

    gate = sealed.post("/api/gate/check", json={"action": "deploy"})
    assert gate.status_code == 200
    body = gate.json()
    assert body["allowed"] is False
    assert body["keys_ready"] is False
    assert any("sealed" in r.lower() for r in body["reasons"])

    # Leave-machine execute must refuse while sealed (metadata keys still exist).
    project = _project(tmp_path)
    plan = sealed.post(
        "/api/deploy/from-cortex",
        json={"project_path": str(project), "subdomain": "app.example.com"},
    )
    assert plan.status_code == 200
    exe = sealed.post(
        f"/api/deploy/{plan.json()['deploy_id']}/execute",
        json={"simulate": True},
    )
    assert exe.status_code == 403
    assert exe.json()["detail"]["keys_ready"] is False
    assert any("sealed" in r.lower() for r in exe.json()["detail"]["reasons"])
