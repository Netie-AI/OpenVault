"""OpenVault HTTP runtime: Caddy + systemd on Hetzner / VPS / AWS, plus CI."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.routers.ship import router as ship_router
from openmw.openvault.ship.aws_guide import build_aws_render_plan
from openmw.openvault.ship.cicd import cicd_plan, detect_cicd
from openmw.openvault.ship.detect import detect_project
from openmw.openvault.ship.server import (
    build_server_plan,
    caddyfile,
    execute_server_plan,
    service_name_for,
    ssm_restart_command,
    systemd_unit,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ship_router)
    return TestClient(app)


def _nextjs(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "^16"},
                "scripts": {"build": "next build", "start": "next start"},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_systemd_unit_and_caddy_reverse_proxy() -> None:
    unit = systemd_unit(
        service_name="openvault-shop",
        workdir="/var/www/openvault-shop",
        start_command="next start",
        port=3000,
    )
    assert "ExecStart=next start" in unit
    assert "Restart=on-failure" in unit
    caddy = caddyfile(hostname="app.example.com", port=3000)
    assert "reverse_proxy 127.0.0.1:3000" in caddy
    assert "handle /healthz" in caddy


def test_caddy_static_file_server() -> None:
    caddy = caddyfile(hostname="site.example.com", port=8080, static_root="/var/www/app")
    assert "file_server" in caddy
    assert "respond /healthz 200" in caddy


def test_ssm_restart_is_systems_manager_shaped() -> None:
    cmd = ssm_restart_command("openvault-shop", instance_id="i-abc")
    assert "aws ssm send-command" in cmd
    assert "--instance-ids i-abc" in cmd
    assert "AWS-RunShellScript" in cmd
    assert "systemctl restart openvault-shop" in cmd


def test_nextjs_server_plan_hetzner(tmp_path: Path) -> None:
    root = _nextjs(tmp_path)
    plan = build_server_plan(
        project_path=str(root),
        hostname="app.example.com",
        vps_host="root@nbg.example",
        target="hetzner",
    )
    assert plan.provider == "hetzner"
    assert plan.ready is True
    assert "next start" in plan.unit_file
    assert "reverse_proxy" in plan.caddyfile
    assert plan.health_url == "https://app.example.com/healthz"
    executed = execute_server_plan(plan, simulate=True)
    assert executed.executed is True
    assert all(s.status in {"simulated", "skipped"} for s in executed.steps)


def test_aws_server_plan_includes_ssm(tmp_path: Path) -> None:
    root = _nextjs(tmp_path)
    plan = build_server_plan(
        project_path=str(root),
        hostname="app.example.com",
        vps_host="i-abc",
        target="aws",
    )
    assert plan.provider == "aws"
    assert any(s.id == "ssm" for s in plan.steps)
    assert "ssm send-command" in plan.ssm_restart


def test_static_server_plan_uses_file_server(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>ok</h1>\n", encoding="utf-8")
    plan = build_server_plan(
        project_path=str(tmp_path),
        hostname="site.example.com",
        target="vps_ssh",
    )
    assert "file_server" in plan.caddyfile
    assert plan.ready is True


def test_cicd_ignores_vercel_json_and_emits_health_workflow(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>ok</h1>\n", encoding="utf-8")
    (tmp_path / "vercel.json").write_text("{}", encoding="utf-8")
    report = detect_cicd(str(tmp_path))
    assert report.vercel is True
    assert report.vercel_ignored is True
    assert report.origin_vercel_ready is False

    payload = cicd_plan(
        str(tmp_path),
        hostname="site.example.com",
        vps_host="box.example",
        provider="hetzner",
        write=True,
    )
    assert payload["replaces_vercel"] is True
    workflow = payload["workflow"]
    assert "https://site.example.com/healthz" in workflow
    assert "systemctl reload caddy" in workflow
    assert "vercel" not in workflow.lower()
    written = Path(payload["written"])
    assert written.is_file()
    assert written.name == "openvault-ship.yml"


def test_service_name_sanitizes() -> None:
    assert service_name_for("/tmp/My App!").startswith("openvault-")


def test_aws_guide_does_not_prefer_vercel() -> None:
    plan = build_aws_render_plan(hostname="app.example.com")
    joined = " ".join(plan.steps)
    assert "Vercel is not used" in joined
    assert "healthz" in joined.lower()


def test_api_server_and_cicd_routes(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>ok</h1>\n", encoding="utf-8")
    client = _client()
    server = client.post(
        "/api/ship/server",
        json={
            "project_path": str(tmp_path),
            "hostname": "site.example.com",
            "target": "hetzner",
            "simulate": True,
        },
    )
    assert server.status_code == 200
    body = server.json()
    assert body["provider"] == "hetzner"
    assert body["executed"] is True

    cicd = client.post(
        "/api/ship/cicd/plan",
        json={
            "project_path": str(tmp_path),
            "hostname": "site.example.com",
            "provider": "aws",
        },
    )
    assert cicd.status_code == 200
    assert cicd.json()["deploy_target"] == "aws"
    stack = detect_project(tmp_path)
    assert stack.framework == "static"
