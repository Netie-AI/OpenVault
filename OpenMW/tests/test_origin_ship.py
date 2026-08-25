"""Type-based auto-ship: Next.js/static -> Origin git + OpenVault Caddy/systemd."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.routers.ship import router as ship_router
from openmw.openvault.ship.detect import detect_project
from openmw.openvault.ship.hosting import ready_to_ship, recommend_host
from openmw.openvault.ship.origin import build_origin_plan, execute_origin_plan, origin_status


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ship_router)
    return TestClient(app)


def test_nextjs_host_kind_is_origin_http(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "^16"},
                "scripts": {"build": "next build", "start": "next start"},
            }
        ),
        encoding="utf-8",
    )
    stack = detect_project(tmp_path)
    assert stack.framework == "nextjs"
    assert stack.host_kind == "edge_http"
    host = recommend_host(stack, hostname="app.example.com")
    assert host.git_target == "cursor_origin"
    assert host.runtime == "vm_process"
    assert host.load_balancer == "caddy"
    assert host.http_auto_update is True
    assert host.needs_vm is True
    assert host.recommended_target == "vps_ssh"


def test_static_site_uses_origin_http(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>hi</h1>\n", encoding="utf-8")
    stack = detect_project(tmp_path)
    assert stack.framework == "static"
    host = recommend_host(stack, hostname="site.example.com")
    assert host.runtime == "caddy_static"
    assert host.needs_static_serve is True
    assert host.load_balancer == "caddy"


def test_fastapi_needs_vm(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
    )
    stack = detect_project(tmp_path)
    host = recommend_host(stack, hostname="api.example.com", vps_host="vm.example.com")
    assert host.host_kind == "process"
    assert host.runtime == "vm_process"
    assert host.recommended_target == "vps_ssh"


def test_ready_to_ship_nextjs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGIN_MODE", "simulate")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "^16"},
                "scripts": {"build": "next build", "start": "next start"},
            }
        ),
        encoding="utf-8",
    )
    report = ready_to_ship(str(tmp_path), hostname="app.example.com", target="vps_ssh")
    assert report.ready is True
    assert report.ready_to_execute is False
    assert report.stack["framework"] == "nextjs"
    ids = {g["id"]: g["status"] for g in report.gates}
    assert ids["detect"] == "pass"
    assert ids["http_auto_update"] == "pass"
    assert ids["execute_host"] == "pending"

    live = ready_to_ship(
        str(tmp_path),
        hostname="app.example.com",
        vps_host="root@1.2.3.4",
        target="vps_ssh",
    )
    assert live.ready_to_execute is True


def test_origin_plan_simulate_nextjs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGIN_MODE", "simulate")
    monkeypatch.setenv("ORIGIN_OWNER", "demo")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "^16"},
                "scripts": {"build": "next build", "start": "next start"},
            }
        ),
        encoding="utf-8",
    )
    plan = build_origin_plan(
        project_path=str(tmp_path),
        hostname="app.example.com",
        owner="demo",
        repo="shop",
    )
    assert plan.vercel_http is False
    assert plan.openvault_http is True
    assert plan.remote_url == "https://origin.cursor.com/demo/shop.git"
    executed = execute_origin_plan(plan, simulate=True)
    assert executed.executed is True
    assert executed.ready is True
    assert {s.id for s in executed.steps} >= {
        "detect",
        "origin_repo",
        "git_push",
        "http_runtime",
        "load_balancer",
    }
    assert "vercel_app" not in {s.id for s in executed.steps}


def test_api_ship_ready_and_auto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGIN_MODE", "simulate")
    (tmp_path / "index.html").write_text("<h1>ok</h1>\n", encoding="utf-8")
    client = _client()
    stacks = client.get("/api/ship/stacks")
    assert stacks.status_code == 200
    assert any(s["id"] == "nextjs" and s["origin_http"] for s in stacks.json()["stacks"])

    status = client.get("/api/ship/origin/status")
    assert status.status_code == 200
    assert status.json()["git_host"] == "https://origin.cursor.com"
    assert "not Vercel" in status.json()["notes"]

    ready = client.post(
        "/api/ship/ready",
        json={"project_path": str(tmp_path), "hostname": "site.example.com"},
    )
    assert ready.status_code == 200
    assert ready.json()["stack"]["framework"] == "static"
    assert ready.json()["ready"] is True
    assert ready.json()["host"]["runtime"] == "caddy_static"

    auto = client.post(
        "/api/ship/auto",
        json={
            "project_path": str(tmp_path),
            "hostname": "site.example.com",
            "simulate": True,
            "owner": "demo",
            "repo": "static-site",
        },
    )
    assert auto.status_code == 200
    body = auto.json()
    assert body["origin"]["vercel_http"] is False
    assert body["origin"]["openvault_http"] is True
    assert body["origin"]["executed"] is True
    assert "origin.cursor.com/demo/static-site.git" in body["origin"]["remote_url"]
    assert body["server"]["executed"] is True
    assert "file_server" in body["server"]["caddyfile"]
    assert body["server"]["health_url"] == "https://site.example.com/healthz"
    assert "curl -fsS" in body["cicd"]["workflow"]
    assert "healthz" in body["cicd"]["workflow"]
    assert "vercel.com" not in body["cicd"]["workflow"].lower()


def test_origin_status_simulate_without_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGIN_MODE", "auto")
    monkeypatch.delenv("ORIGIN_TOKEN", raising=False)
    status = origin_status()
    assert status["mode"] in {"simulate", "cli"}
    assert status["ready"] is True
