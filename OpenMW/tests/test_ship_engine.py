"""In-process ship engine + GitHub library (stolen FreeBuild concepts)."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.github_auth import parse_github_url
from openmw.openvault.ship.library import inspect_github_url
from openmw.openvault.ship.engine import run_ship_engine
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


def test_parse_github_url() -> None:
    assert parse_github_url("https://github.com/oblien/openship") == ("oblien", "openship")
    assert parse_github_url("git@github.com:oblien/openship.git") is None  # ssh form not in regex
    assert inspect_github_url("https://github.com/foo/bar").get("ok") is True


def test_engine_local_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    monkeypatch.setenv("OPENSHIP_MODE", "simulate")
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    out = run_ship_engine(
        target="local_demo",
        project_path=str(app_dir),
        hostname="app.example.com",
    )
    assert out["ok"] is True
    assert out["deployment"]["stack"]["primary"] == "node"
    assert out["deployment"]["steps"]


def test_ship_github_and_library_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    monkeypatch.setenv("OPENSHIP_MODE", "simulate")
    project = tmp_path / "app"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=seal)
    rec = vault.create(
        label="k",
        provider="openai",
        secret="sk-test-aaaaaaaaaaaa",
        role="primary",
        base_url="https://api.openai.com/v1",
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)

    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    client = TestClient(app)

    lib = client.get("/api/ship/library")
    assert lib.status_code == 200
    assert "connection" in lib.json()

    insp = client.post(
        "/api/ship/library/inspect",
        json={"path": str(project)},
    )
    assert insp.status_code == 200
    assert insp.json()["stack"]["primary"] == "python"

    connect = client.post("/api/ship/github/connect")
    assert connect.status_code == 200
    # gh may or may not be present in CI — either ok with command or error
    body = connect.json()
    assert "ok" in body or "error" in body or "command" in body

    eng = client.post(
        "/api/ship/engine",
        json={
            "target": "aws_guide",
            "project_path": str(project),
            "hostname": "api.example.com",
        },
    )
    assert eng.status_code == 200
    assert eng.json()["deployment"]["target"] == "aws_guide"

    press = client.post(
        "/api/deploy/one-press",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "target": "local_demo",
            "simulate": True,
            "auto_execute": True,
        },
    )
    assert press.status_code == 200
    assert "engine" in press.json()
