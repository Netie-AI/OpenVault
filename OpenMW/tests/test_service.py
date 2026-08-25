"""OpenVault Service login, SKU prices, auto-host (not the laptop)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.routers.ship import router as ship_router
from openmw.openvault.ship.service import (
    connect_service,
    login_service,
    quote,
    service_catalog,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ship_router)
    return TestClient(app)


def test_catalog_suggested_wraps_aws_and_vps() -> None:
    catalog = service_catalog()
    assert catalog["laptop"] is False
    assert catalog["suggested_sku"] == "ov_hosted"
    hosted = next(s for s in catalog["skus"] if s["id"] == "ov_hosted")
    assert hosted["monthly_usd"] == 24.0
    assert "aws_lightsail" in hosted["wraps"]
    assert "vps" in hosted["wraps"]
    fast = next(s for s in catalog["skus"] if s["id"] == "ov_fast")
    assert fast["monthly_usd"] == 79.0
    byo = next(s for s in catalog["skus"] if s["id"] == "byo_aws")
    assert byo["customer_pays_infra"] is True
    assert byo["monthly_usd"] == 9.0


def test_login_openvault_assigns_box(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    session = login_service(email="ops@acme.example", display_name="Acme")
    assert session.laptop is False
    assert session.login_kind == "openvault"
    assert session.sku_id == "ov_hosted"
    assert session.hostname == "acme.openvault.app"
    assert session.vps_host.startswith("ov-hosted-")
    assert session.connected is True


def test_connect_aws_mcp_does_not_store_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    session = login_service(email="ops@acme.example", login_kind="openvault")
    connected = connect_service(
        session.session_id,
        login_kind="aws",
        aws_region="eu-central-1",
        aws_account_hint="123456789012",
        secret="AKIA-SHOULD-NEVER-PERSIST",
    )
    assert connected.aws_mcp is True
    assert connected.sku_id == "byo_aws"
    dumped = connected.to_dict()
    assert "AKIA" not in str(dumped)
    assert dumped["laptop"] is False


def test_own_server_is_fast_sku(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    session = login_service(email="ops@acme.example", login_kind="own_server")
    assert session.sku_id == "ov_fast"
    billed = quote(login_kind="own_server")
    assert billed["monthly_usd"] == 79.0
    assert billed["laptop"] is False


def test_api_login_quote_auto_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    (tmp_path / "index.html").write_text("<h1>ok</h1>\n", encoding="utf-8")
    client = _client()
    catalog = client.get("/api/service/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["suggested_sku"] == "ov_hosted"

    login = client.post(
        "/api/service/login",
        json={"email": "ops@acme.example", "display_name": "Acme", "login_kind": "openvault"},
    )
    assert login.status_code == 200
    session_id = login.json()["session_id"]
    assert login.json()["sku"]["monthly_usd"] == 24.0

    billed = client.post(
        "/api/service/quote",
        json={"login_kind": "openvault", "project_path": str(tmp_path)},
    )
    assert billed.status_code == 200
    assert billed.json()["monthly_usd"] == 24.0
    assert billed.json()["load_balancer"] == "caddy"

    hosted = client.post(
        "/api/service/auto-host",
        json={"session_id": session_id, "project_path": str(tmp_path), "simulate": True},
    )
    assert hosted.status_code == 200
    body = hosted.json()
    assert body["laptop"] is False
    assert body["server"]["executed"] is True
    assert "file_server" in body["server"]["caddyfile"]
    assert body["quote"]["monthly_usd"] == 24.0

    auto = client.post(
        "/api/ship/auto",
        json={
            "project_path": str(tmp_path),
            "session_id": session_id,
            "simulate": True,
            "hostname": "site.example.com",
        },
    )
    assert auto.status_code == 200
    assert auto.json()["laptop"] is False
    assert auto.json()["quote"]["monthly_usd"] == 24.0
    assert auto.json()["service"]["session_id"] == session_id


def test_login_rejects_laptop_kind() -> None:
    client = _client()
    res = client.post(
        "/api/service/login",
        json={"email": "ops@acme.example", "login_kind": "laptop"},
    )
    assert res.status_code == 400
