"""Account custody, key kill/rotate, FreeBuild plan, Playwright smoke gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.openship import build_openship_plan, execute_openship_plan
from openmw.openvault.ship.playwright_smoke import run_playwright_smoke
from openmw.openvault.vault.accounts import AccountStore, suggest_netie_email
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.setenv("OPENSHIP_MODE", "simulate")
    monkeypatch.setenv("OPENVAULT_PLAYWRIGHT_MODE", "dry")
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
    # Custody mutations are loopback-only; TestClient's default host is
    # "testclient", which the guard correctly rejects. See test_secret_reveal_gate.
    return TestClient(app, client=("127.0.0.1", 5555))


def test_suggest_netie_email() -> None:
    assert suggest_netie_email("Acme Ops!").endswith("@netie.ai")
    assert "acme" in suggest_netie_email("Acme Ops!")


def test_create_netie_account_with_relay(client: TestClient) -> None:
    res = client.post(
        "/api/accounts",
        json={
            "display_name": "Acme",
            "auth_provider": "netie_email",
            "local_part": "acme",
            "allocate_relay": True,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "acme@netie.ai"
    assert body["relay_address"] and body["relay_address"].endswith("@relay.netie.ai")
    assert body["auth_provider"] == "netie_email"

    listed = client.get("/api/accounts")
    assert listed.status_code == 200
    assert len(listed.json()["accounts"]) == 1


def test_google_and_email_signup(client: TestClient) -> None:
    g = client.post(
        "/api/accounts",
        json={
            "display_name": "G User",
            "auth_provider": "google",
            "email": "person@gmail.com",
        },
    )
    assert g.status_code == 200
    assert g.json()["email"] == "person@gmail.com"

    e = client.post(
        "/api/accounts",
        json={
            "display_name": "E User",
            "auth_provider": "email",
            "email": "ops@example.com",
        },
    )
    assert e.status_code == 200


def test_operator_managed_key_and_incident_kill(client: TestClient) -> None:
    acct = client.post(
        "/api/accounts",
        json={"display_name": "Tenant", "auth_provider": "netie_email", "local_part": "tenant"},
    ).json()
    account_id = acct["id"]

    key = client.post(
        f"/api/accounts/{account_id}/keys",
        json={
            "label": "primary openai",
            "provider": "openai",
            "secret": "sk-old-secret-aaaaaaaa",
            "role": "primary",
        },
    )
    assert key.status_code == 200
    assert key.json()["account_id"] == account_id
    key_id = key.json()["id"]

    rotated = client.post(
        f"/api/keys/{key_id}/rotate",
        json={"new_secret": "sk-new-secret-bbbbbbbb"},
    )
    assert rotated.status_code == 200
    assert rotated.json()["lifecycle"] == "active"

    incident = client.post(
        f"/api/accounts/{account_id}/incident",
        json={
            "reason": "hack_attempt",
            "replacement_secrets": {"openai": "sk-replacement-cccccccc"},
            "suspend_account": True,
        },
    )
    assert incident.status_code == 200
    body = incident.json()
    assert len(body["killed"]) >= 1
    assert len(body["replacements"]) == 1
    assert body["account"]["status"] == "compromised"

    bundle = client.get(f"/api/accounts/{account_id}")
    assert bundle.status_code == 200
    assert "operator_can" in bundle.json()["custody"]
    lifecycles = {k["lifecycle"] for k in bundle.json()["keys"]}
    assert "compromised" in lifecycles
    assert "active" in lifecycles


def test_revoke_key(client: TestClient) -> None:
    acct = client.post(
        "/api/accounts",
        json={"display_name": "R", "auth_provider": "netie_email", "local_part": "r"},
    ).json()
    key = client.post(
        f"/api/accounts/{acct['id']}/keys",
        json={"label": "k", "provider": "groq", "secret": "gsk-test", "role": "cheap"},
    ).json()
    rev = client.post(f"/api/keys/{key['id']}/revoke", json={"reason": "leak"})
    assert rev.status_code == 200
    assert rev.json()["lifecycle"] == "revoked"
    assert rev.json()["enabled"] is False


def test_openship_full_plan_and_execute(home: Path, tmp_path: Path) -> None:
    project = tmp_path / "app"
    project.mkdir()
    (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    plan = build_openship_plan(
        project_path=str(project),
        subdomain="app.example.com",
        action="install",
    )
    step_ids = {s.id for s in plan.steps}
    assert {"detect", "subdomain", "tls", "mail", "build", "apps_services", "roll"} <= step_ids
    executed = execute_openship_plan(plan, simulate=True)
    assert executed.executed is True
    assert all(s.status in ("pass", "simulated", "pending", "skipped") for s in executed.steps)


def test_playwright_smoke_dry_mode(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_PLAYWRIGHT_MODE", "dry")

    class _Resp:
        status_code = 200
        elapsed = type("E", (), {"total_seconds": lambda self: 0.01})()

    class _Client:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def get(self, url: str) -> _Resp:
            assert url.startswith("http")
            return _Resp()

    monkeypatch.setattr("httpx.Client", _Client)
    result = run_playwright_smoke("https://example.com", mode="dry")
    assert result.status == "pass"
    assert result.artifact_path is not None
    assert Path(result.artifact_path).is_file()


def test_deploy_smoke_and_execute_api(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "web"
    project.mkdir()
    (project / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    # seed a healthy key so keys gate can pass after precheck mark via vault create+set
    # (API create leaves unknown — use vault through keys and accept fail/pending for keys)
    class _Resp:
        status_code = 200
        elapsed = type("E", (), {"total_seconds": lambda self: 0.01})()

    class _Client:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def get(self, url: str) -> _Resp:
            return _Resp()

    monkeypatch.setattr("httpx.Client", _Client)

    # Leave-machine execute calls check_gate — seed a cloud key so the gate opens.
    seeded = client.post(
        "/api/keys",
        json={
            "label": "groq",
            "provider": "groq",
            "secret": "gsk-test-accounts-execute",
            "role": "free",
        },
    )
    assert seeded.status_code == 200, seeded.text

    plan = client.post(
        "/api/deploy/from-cortex",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "source": "airgpt",
            "smoke_url": "https://example.com",
        },
    )
    assert plan.status_code == 200
    deploy_id = plan.json()["deploy_id"]
    assert plan.json()["ship_id"]

    smoke = client.post(
        f"/api/deploy/{deploy_id}/playwright-smoke",
        json={"url": "https://example.com"},
    )
    assert smoke.status_code == 200
    gate = next(g for g in smoke.json()["gates"] if g["id"] == "playwright")
    assert gate["status"] == "pass"
    assert smoke.json()["smoke_id"]

    exe = client.post(f"/api/deploy/{deploy_id}/execute", json={"simulate": True})
    assert exe.status_code == 200
    assert exe.json()["openship"]["executed"] is True
    roll = next(g for g in exe.json()["gates"] if g["id"] == "roll")
    assert roll["status"] == "pass"

    direct = client.post(
        "/api/playwright/smoke",
        json={"url": "https://example.com", "mode": "dry"},
    )
    assert direct.status_code == 200
    assert direct.json()["status"] == "pass"

    ship = client.post(
        "/api/freebuild/plan",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "execute": True,
            "simulate": True,
        },
    )
    assert ship.status_code == 200
    assert ship.json()["executed"] is True
