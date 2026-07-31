"""Ship targets, AWS guide, bill budget, FreeBuild client presence."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.aws_guide import build_aws_render_plan
from openmw.openvault.ship.cloud_targets import build_ship_blueprint
from openmw.openvault.ship.openship_client import adapter_status
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


def test_aws_render_plan_has_core_services() -> None:
    plan = build_aws_render_plan(hostname="app.example.com")
    names = {r.service for r in plan.resources}
    assert "S3" in names
    assert "CloudFront" in names
    assert "ALB" in names
    assert any("Lambda" in n or "Fargate" in n for n in names)
    assert plan.bill_controls
    assert plan.hetzner_alternative


def test_blueprint_and_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    monkeypatch.setenv("OPENSHIP_MODE", "simulate")
    bp = build_ship_blueprint(
        target="aws_guide",
        project_path=str(tmp_path),
        hostname="app.example.com",
        monthly_cap_usd=10.0,
    )
    assert bp["target"]["id"] == "aws_guide"
    assert bp["aws_plan"] is not None
    assert bp["budget"]["monthly_cap_usd"] == 10.0
    assert adapter_status()["effective"] in ("simulate", "api", "cli")


def test_ship_api_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    monkeypatch.setenv("OPENSHIP_MODE", "simulate")
    project = tmp_path / "app"
    project.mkdir()
    (project / "package.json").write_text('{"name":"x"}', encoding="utf-8")

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

    targets = client.get("/api/ship/targets")
    assert targets.status_code == 200
    ids = {t["id"] for t in targets.json()["targets"]}
    assert "openship_cloud" in ids
    assert "vps_ssh" in ids
    assert "aws_guide" in ids

    bud = client.put(
        "/api/ship/budget",
        json={"monthly_cap_usd": 40.0, "hard_stop": True},
    )
    assert bud.status_code == 200
    assert bud.json()["monthly_cap_usd"] == 40.0

    aws = client.post("/api/ship/aws-plan", json={"hostname": "api.example.com"})
    assert aws.status_code == 200
    assert aws.json()["resources"]

    press = client.post(
        "/api/deploy/one-press",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "target": "local_demo",
            "simulate": True,
            "auto_execute": True,
            "monthly_cap_usd": 40.0,
        },
    )
    assert press.status_code == 200
    body = press.json()
    assert body["blueprint"]["target"]["id"] == "local_demo"
    assert body["one_press"]["executed"] is True
