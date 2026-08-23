"""Gate deny must append durable audit; ignore_gate WARN+deny (OpenVault#23)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.accounts import AccountStore
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault

BYPASS_FLAGS = ("bypass", "bypass_gate", "force", "skip_rules", "ignore_gate")


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


def _audit_lines(home: Path) -> list[dict]:
    path = home / "secret_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "web"
    project.mkdir()
    (project / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    return project


@pytest.mark.parametrize("flag", BYPASS_FLAGS)
def test_bypass_flags_deny_and_audit(client: TestClient, home: Path, flag: str) -> None:
    before = len(_audit_lines(home))
    res = client.post("/api/gate/check", json={"action": "deploy", flag: True})
    assert res.status_code == 200
    body = res.json()
    assert body["allowed"] is False
    assert body["reasons"]
    assert any("WARN" in r or "bypass" in r.lower() for r in body["reasons"])

    lines = _audit_lines(home)
    assert len(lines) == before + 1
    entry = lines[-1]
    assert entry["event"] == "gate_bypass_attempt"
    assert entry["action"] == "deploy"
    assert entry["reasons"]
    assert "client" in entry
    # Never secret material in the audit line.
    blob = json.dumps(entry)
    assert "gsk-" not in blob
    assert "sk-" not in blob


def test_gate_check_deny_audits(client: TestClient, home: Path) -> None:
    """Empty vault deploy deny leaves a gate_denied line (not bypass)."""
    before = len(_audit_lines(home))
    res = client.post("/api/gate/check", json={"action": "deploy"})
    assert res.status_code == 200
    body = res.json()
    assert body["allowed"] is False
    assert body["keys_ready"] is False

    lines = _audit_lines(home)
    assert len(lines) == before + 1
    entry = lines[-1]
    assert entry["event"] == "gate_denied"
    assert entry["action"] == "deploy"
    assert entry.get("source") == "gate_check"
    assert entry["reasons"]
    assert "client" in entry


def test_leave_execute_deny_audits(client: TestClient, home: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    plan = client.post(
        "/api/deploy/from-cortex",
        json={"project_path": str(project), "subdomain": "app.example.com"},
    )
    assert plan.status_code == 200
    deploy_id = plan.json()["deploy_id"]

    before = len(_audit_lines(home))
    exe = client.post(f"/api/deploy/{deploy_id}/execute", json={"simulate": True})
    assert exe.status_code == 403
    assert exe.json()["detail"]["allowed"] is False

    lines = _audit_lines(home)
    assert len(lines) == before + 1
    entry = lines[-1]
    assert entry["event"] == "gate_denied"
    assert entry.get("source") == "leave_execute"
    assert entry["reasons"]
    assert entry.get("client") == "127.0.0.1"


def test_ignore_gate_not_silently_dropped(client: TestClient) -> None:
    """Pydantic must accept ignore_gate; firewall path must WARN+deny."""
    res = client.post("/api/gate/check", json={"action": "run", "ignore_gate": True})
    assert res.status_code == 200
    body = res.json()
    assert body["allowed"] is False
    assert body["firewall"]["level"] in ("deny", "warn") or not body["firewall"]["allowed"]
    joined = " ".join(body["reasons"]).upper()
    assert "WARN" in joined or "BYPASS" in joined
