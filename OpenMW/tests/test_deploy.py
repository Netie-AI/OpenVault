"""Tests for auto-detect and deploy gate pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.detect import detect_project
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


def test_detect_python_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    stack = detect_project(tmp_path)
    assert stack.primary == "python"
    assert stack.confidence >= 0.8
    assert "pyproject.toml" in stack.signals


def test_detect_prefers_compose(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    stack = detect_project(tmp_path)
    assert stack.primary == "docker-compose"


def test_deploy_from_cortex_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    project = tmp_path / "app"
    project.mkdir()
    (project / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=seal)
    record = vault.create(
        label="primary",
        provider="openai",
        secret="sk-test-aaaaaaaaaaaa",
        role="primary",
        base_url="https://api.openai.com/v1",
    )
    vault.set_precheck(record.id, status="ok", latency_ms=5.0, error=None)

    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    client = TestClient(app)

    detect = client.post("/api/detect", json={"project_path": str(project)})
    assert detect.status_code == 200
    assert detect.json()["primary"] == "node"

    plan = client.post(
        "/api/deploy/from-cortex",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "source": "airgpt",
            "intent": "deploy_to_web",
        },
    )
    assert plan.status_code == 200
    body = plan.json()
    assert body["source"] == "airgpt"
    assert body["stack"]["primary"] == "node"
    gate_ids = {g["id"]: g["status"] for g in body["gates"]}
    assert gate_ids["auto_detect"] == "pass"
    assert gate_ids["keys"] == "pass"
    assert gate_ids["subdomain"] == "pass"
    assert "email_auth" in gate_ids
    assert "playwright" in gate_ids
    assert "openship" in gate_ids

    listed = client.get("/api/deploy")
    assert listed.status_code == 200
    assert any(d["deploy_id"] == body["deploy_id"] for d in listed.json()["deploys"])

    one = client.get(f"/api/deploy/{body['deploy_id']}")
    assert one.status_code == 200
