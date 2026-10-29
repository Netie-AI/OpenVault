"""Super-app plan: route, ship, both, and no secrets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.routers.superapp import build_superapp_router
from openmw.openvault.superapp.plan import SuperappInputError, plan_superapp
from cryptography.fernet import Fernet

from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


def test_empty_input_is_rejected() -> None:
    with pytest.raises(SuperappInputError):
        plan_superapp("   ")


def test_relative_path_is_rejected() -> None:
    with pytest.raises(SuperappInputError):
        plan_superapp("ship it", project_path="relative/app")


def test_chat_picks_named_model_when_keyed() -> None:
    plan = plan_superapp("ask groq to chat", keyed_providers={"groq"})
    assert plan["lanes"] == ["route"]
    assert plan["parallel"] is False
    assert plan["model"]["provider"] == "groq"
    assert plan["model"]["key_present"] is True
    assert plan["model"]["model"]
    assert plan["tasks"][0]["engine"] == "freeroute"
    assert "20128" in plan["tasks"][0]["url"]


def test_light_app_recommends_a_ready_static_cloud(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    plan = plan_superapp(
        "ask groq and ship this small landing",
        project_path=str(tmp_path),
        keyed_providers={"cloudflare", "groq"},
    )
    assert plan["lanes"] == ["route", "ship"]
    assert plan["parallel"] is True
    assert plan["light_app"] is True
    assert plan["app"]["category"] == "static"
    assert plan["recommended_cloud"] == "cloudflare_pages"
    cloud_ids = {row["id"] for row in plan["clouds"]}
    assert cloud_ids == {
        "cloudflare_pages",
        "coolify",
        "netlify",
        "spaceship_ftp",
        "vps_ssh",
    }
    ready = {row["id"] for row in plan["clouds"] if row["ready"]}
    assert ready == {"cloudflare_pages"}
    assert len(plan["tasks"]) == 2
    assert {task["parallel_group"] for task in plan["tasks"]} == {"super"}


def test_plan_has_no_secrets() -> None:
    plan = plan_superapp("deploy the app", keyed_providers={"netlify"})
    blob = json.dumps(plan)
    assert "secret" not in blob
    assert "sk-" not in blob
    assert plan["keys"] == "provider keys stay in OpenVault KeyVault"


def test_api_plan_reads_provider_ids_not_secrets(tmp_path: Path) -> None:
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=seal)
    vault.create(label="groq", provider="groq", secret="gsk-test-secret-value")
    app = FastAPI()
    app.include_router(build_superapp_router(vault))
    client = TestClient(app)
    response = client.post("/api/superapp/plan", json={"text": "ask groq to chat"})
    assert response.status_code == 200
    body = response.json()
    assert body["model"]["provider"] == "groq"
    assert "gsk-test-secret-value" not in response.text


def test_api_label_marks_cloud_ready(tmp_path: Path) -> None:
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=seal)
    vault.create(label="cloudflare pages", provider="custom", secret="cf-token-do-not-leak")
    app = FastAPI()
    app.include_router(build_superapp_router(vault))
    response = TestClient(app).post(
        "/api/superapp/plan",
        json={"text": "ship this small landing", "project_path": str(tmp_path / "site")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recommended_cloud"] == "cloudflare_pages"
    assert "cf-token-do-not-leak" not in response.text


def test_api_empty_body_is_400(tmp_path: Path) -> None:
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=seal)
    app = FastAPI()
    app.include_router(build_superapp_router(vault))
    response = TestClient(app).post("/api/superapp/plan", json={})
    assert response.status_code == 400
