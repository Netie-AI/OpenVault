"""Stripe SKU checkout + ship onto *.netie.ai. Isolated from AirGPT and DMS."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.routers.ship import router as ship_router
from openmw.openvault.ship.pipeline import NETIE_HTTP_SUFFIX, ship_to_netie
from openmw.openvault.ship.service import login_service
from openmw.openvault.ship.stripe_billing import (
    apply_checkout_event,
    confirm_checkout,
    create_checkout,
    price_id_for,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ship_router)
    return TestClient(app)


def test_price_ids_are_netie_test_mode() -> None:
    assert price_id_for("ov_hosted").startswith("price_")
    assert price_id_for("ov_fast").startswith("price_")
    assert price_id_for("byo_aws").startswith("price_")
    assert price_id_for("byo_vps").startswith("price_")


def test_catalog_exposes_stripe_test_prices() -> None:
    from openmw.openvault.ship.service import service_catalog

    catalog = service_catalog()
    assert catalog["stripe"]["mode"] == "test"
    assert catalog["stripe"]["checkout"] == "hosted_subscription"
    hosted = next(s for s in catalog["skus"] if s["id"] == "ov_hosted")
    assert hosted["stripe_price_id"] == price_id_for("ov_hosted")


def test_default_login_stays_on_openvault_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    session = login_service(email="ops@acme.example", display_name="Acme")
    assert session.hostname == "acme.openvault.app"
    assert not session.hostname.endswith(".netie.ai")


def test_stripe_modules_do_not_import_airgpt_or_dms() -> None:
    root = Path(__file__).resolve().parents[1] / "openmw" / "openvault" / "ship"
    for name in ("pipeline.py", "stripe_billing.py"):
        for line in (root / name).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                lower = stripped.lower()
                assert "airgpt" not in lower
                assert "vault.trust" not in lower
                assert "trust_root" not in lower


def test_simulate_checkout_then_webhook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    monkeypatch.setenv("STRIPE_MODE", "simulate")
    session = login_service(email="ops@netie.ai", display_name="ops")
    checkout = create_checkout(session.session_id)
    assert checkout["simulated"] is True
    assert checkout["livemode"] is False
    assert checkout["mode"] == "subscription"
    assert checkout["price_id"] == price_id_for("ov_hosted")
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": checkout["id"],
                "subscription": "sub_test_1",
                "metadata": {"ov_session": session.session_id, "sku": "ov_hosted"},
            }
        },
    }
    applied = apply_checkout_event(event)
    assert applied["ok"] is True
    assert applied["session"]["billed"] is True


def test_ship_to_netie_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    monkeypatch.setenv("STRIPE_MODE", "simulate")
    (tmp_path / "index.html").write_text("<h1>netie</h1>\n", encoding="utf-8")
    result = ship_to_netie(
        email="ship@netie.ai",
        project_path=str(tmp_path),
        display_name="demo",
        sku_id="ov_hosted",
        simulate=True,
    )
    assert result["ok"] is True
    assert result["airgpt"] is False
    assert result["dms"] is False
    assert result["laptop"] is False
    assert result["domain"] == NETIE_HTTP_SUFFIX
    assert result["hostname"] == "demo.netie.ai"
    assert result["checkout"]["simulated"] is True
    assert result["session"]["billed"] is True
    assert "file_server" in result["server"]["caddyfile"]
    assert "demo.netie.ai" in result["server"]["caddyfile"]
    assert result["server"]["health_url"] == "https://demo.netie.ai/healthz"


def test_api_checkout_and_ship_netie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    monkeypatch.setenv("STRIPE_MODE", "simulate")
    (tmp_path / "index.html").write_text("<h1>ok</h1>\n", encoding="utf-8")
    client = _client()
    login = client.post(
        "/api/service/login",
        json={"email": "ops@netie.ai", "display_name": "ops", "login_kind": "openvault"},
    )
    assert login.status_code == 200
    sid = login.json()["session_id"]
    checkout = client.post("/api/service/checkout", json={"session_id": sid})
    assert checkout.status_code == 200
    assert checkout.json()["price_id"].startswith("price_")
    paid = client.post(
        "/api/service/checkout/confirm",
        json={"session_id": sid, "checkout_id": checkout.json()["id"]},
    )
    assert paid.status_code == 200
    assert paid.json()["session"]["billed"] is True

    shipped = client.post(
        "/api/service/ship-netie",
        json={
            "email": "pipeline@netie.ai",
            "display_name": "pipeline",
            "project_path": str(tmp_path),
            "sku_id": "ov_hosted",
            "simulate": True,
        },
    )
    assert shipped.status_code == 200
    body = shipped.json()
    assert body["hostname"] == "pipeline.netie.ai"
    assert body["airgpt"] is False
    assert body["dms"] is False
    assert body["ok"] is True


def test_confirm_checkout_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    session = login_service(email="ops@netie.ai")
    create_checkout(session.session_id)
    paid = confirm_checkout(session.session_id)
    assert paid["ok"] is True
    assert paid["session"]["billed"] is True
