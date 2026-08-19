"""In-process ship engine + GitHub library (stolen FreeBuild concepts)."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.engine import (
    DeployInProgressError,
    active_deploys,
    deploy_key,
    project_deploy_lock,
    run_ship_engine,
)
from openmw.openvault.ship.github_auth import parse_github_url
from openmw.openvault.ship.library import inspect_github_url
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
    assert out["deployment"]["public_url"] == ""
    assert out["deployment"]["mode"] == "simulated"
    host = next(s for s in out["deployment"]["steps"] if s["id"] == "host")
    assert host["status"] == "simulated"
    assert "non-production" in host["detail"]


def test_engine_openship_cloud_simulate_no_fake_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate must stay valid — but must never invent https://…opsh.io / hostname."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    monkeypatch.setenv("OPENSHIP_MODE", "simulate")
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    out = run_ship_engine(
        target="openship_cloud",
        project_path=str(app_dir),
        hostname="app.example.com",
    )
    assert out["ok"] is True
    dep = out["deployment"]
    assert dep["public_url"] == ""
    assert dep["mode"] == "simulated"
    assert "opsh.io" not in dep["public_url"]
    assert not dep["public_url"].startswith("https://app.example.com")
    host = next(s for s in dep["steps"] if s["id"] == "host")
    assert host["status"] == "simulated"
    assert "non-production" in host["detail"].lower() or "simulate" in host["detail"].lower()


def test_engine_aws_guide_no_fake_live_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    out = run_ship_engine(
        target="aws_guide",
        project_path=str(app_dir),
        hostname="api.example.com",
    )
    assert out["ok"] is True
    assert out["deployment"]["public_url"] == ""
    assert out["deployment"]["mode"] == "guide"


def test_engine_openship_cloud_remote_pass_without_url_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remote 'success' without an observed URL must not invent *.opsh.io."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    monkeypatch.setenv("OPENSHIP_URL", "https://openship.example")
    monkeypatch.setenv("OPENSHIP_TOKEN", "tok")
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    class _FakeClient:
        available = True

        def build_access(self, _payload: dict) -> dict:
            return {"ok": True, "deployment_id": "dep123", "http_status": 200}

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "openmw.openvault.ship.openship_client.OpenShipClient",
        lambda: _FakeClient(),
    )
    out = run_ship_engine(
        target="openship_cloud",
        project_path=str(app_dir),
        hostname="app.example.com",
        prefer_remote_openship=True,
    )
    assert out["ok"] is False
    dep = out["deployment"]
    assert dep["public_url"] == ""
    host = next(s for s in dep["steps"] if s["id"] == "host")
    assert host["status"] == "fail"
    assert "opsh.io" not in dep["public_url"]


def test_engine_openship_cloud_remote_observed_url_is_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    monkeypatch.setenv("OPENSHIP_URL", "https://openship.example")
    monkeypatch.setenv("OPENSHIP_TOKEN", "tok")
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    class _FakeClient:
        available = True

        def build_access(self, _payload: dict) -> dict:
            return {
                "ok": True,
                "deployment_id": "dep456",
                "http_status": 200,
                "url": "https://demo.opsh.io",
            }

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "openmw.openvault.ship.openship_client.OpenShipClient",
        lambda: _FakeClient(),
    )
    out = run_ship_engine(
        target="openship_cloud",
        project_path=str(app_dir),
        prefer_remote_openship=True,
    )
    assert out["ok"] is True
    dep = out["deployment"]
    assert dep["public_url"] == "https://demo.opsh.io"
    assert dep["mode"] == "live"
    host = next(s for s in dep["steps"] if s["id"] == "host")
    assert host["status"] == "pass"


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
    client = TestClient(app, client=("127.0.0.1", 5555))

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


def test_engine_vps_without_a_server_is_pending_not_a_fake_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No box connected yet is a to-do for the user, not a deploy that "passed"."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    out = run_ship_engine(
        target="vps_ssh",
        project_path=str(app_dir),
        hostname="app.example.com",
    )
    host = next(s for s in out["deployment"]["steps"] if s["id"] == "host")
    assert host["status"] == "pending"
    assert out["deployment"]["public_url"] == ""
    assert out["deployment"]["mode"] != "live"


def test_engine_vps_reports_the_adapter_failure_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable box fails the host step with the reason — never a live URL."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    from openmw.openvault.ship.hosts import vps_ssh as mod

    class DeadRunner:
        def run(self, script: str, *, timeout_s: float | None = None) -> mod.RunResult:
            return mod.RunResult(255, stderr="ssh: connect to host port 22: Timed out")

        def put_bytes(self, path: str, data: bytes, *, mode: str = "600") -> mod.RunResult:
            return mod.RunResult(255)

    monkeypatch.setattr(
        mod.VpsSshAdapter, "runner", property(lambda self: DeadRunner()), raising=False
    )
    out = run_ship_engine(
        target="vps_ssh",
        project_path=str(app_dir),
        hostname="app.example.com",
        vps_host="203.0.113.10",
    )
    host = next(s for s in out["deployment"]["steps"] if s["id"] == "host")
    assert host["status"] == "fail"
    assert "Timed out" in host["detail"]
    assert out["deployment"]["public_url"] == ""


def test_engine_vps_without_a_server_is_blocked_not_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending host step used to satisfy `ready`, so the route answered ok=true.

    Asserted on the HTTP JSON, because that is the layer the customer receives.
    """
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    monkeypatch.setenv("OPENVAULT_KEY", Fernet.generate_key().decode())
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    # vps_ssh is a target that really leaves the machine, so the route is now
    # behind the leave gate. Satisfy the gate, or this asserts 403 and proves
    # nothing about how a pending host step is classified.
    gate_vault = KeyVault(db_path=tmp_path / "keys.db", seal=Seal())
    rec = gate_vault.create(
        label="gate-key",
        provider="openai",
        secret="sk-test-gate-aaaaaaaa",
        role="primary",
        base_url="https://example.invalid/v1",
    )
    gate_vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)

    client = TestClient(
        create_app(vault=gate_vault, mock_health=True, enable_precheck_loop=False),
        client=("127.0.0.1", 5555),
    )
    resp = client.post(
        "/api/ship/engine",
        json={
            "target": "vps_ssh",
            "project_path": str(app_dir),
            "hostname": "app.example.com",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False, "no server address means the deploy did not happen"
    assert body["deployment"]["status"] == "blocked"
    assert body["deployment"]["ready"] is False
    assert body["deployment"]["public_url"] == ""


def test_build_requirement_comes_from_the_adapter_not_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detect -> build -> ship must complete without the caller guessing.

    one-press hardcoded run_build=False and the web UI sent it only for
    Cloudflare Pages, so a Netlify deploy reached the host step with nothing
    built and refused - a dead end the user could not fix from the UI.
    """
    from openmw.openvault.ship.hosts import needs_local_build

    # The adapters that upload a directory say so; the ones that build elsewhere
    # do not, so we never burn minutes building for them.
    assert needs_local_build("cloudflare_pages") is True
    assert needs_local_build("netlify") is True
    assert needs_local_build("coolify") is False
    assert needs_local_build("vps_ssh") is False
    assert needs_local_build("local_demo") is False

    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    app_dir = tmp_path / "site"
    app_dir.mkdir()
    (app_dir / "package.json").write_text(
        '{"name":"site","scripts":{"build":"echo built"}}', encoding="utf-8"
    )

    # run_build not passed at all, exactly as one-press calls it.
    out = run_ship_engine(
        target="netlify",
        project_path=str(app_dir),
        hostname="site.example.com",
    )
    steps = {s["id"]: s for s in out["deployment"]["steps"]}
    assert "build" in steps
    assert steps["build"]["title"] == "Local build", (
        "a target that uploads an artifact must reach the real build step, "
        f"got {steps['build']['title']!r}"
    )
    # It fails without a Netlify token, but on the token - not on "nothing was built".
    assert "nothing was built" not in steps["host"]["detail"]


def test_ship_engine_route_is_gated_for_targets_that_leave_the_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/api/ship/engine had no leave gate at all.

    Harmless while every real target was a stub; once the VPS adapter landed it
    was an unauthenticated route that SSHes into a box and swaps live traffic.
    """
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    client = TestClient(
        create_app(mock_health=True, enable_precheck_loop=False), client=("127.0.0.1", 5555)
    )
    body = {
        "project_path": str(app_dir),
        "hostname": "app.example.com",
        "vps_host": "203.0.113.10",
    }
    blocked = client.post("/api/ship/engine", json={**body, "target": "vps_ssh"})
    assert blocked.status_code == 403, "an empty vault must not be able to deploy to a box"
    assert blocked.json()["detail"]["allowed"] is False

    # R-0005: the gate must not start refusing targets that never leave.
    local = client.post("/api/ship/engine", json={**body, "target": "local_demo"})
    assert local.status_code == 200


def test_engine_local_demo_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-0005: the stricter rule must not start refusing work that really worked."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    out = run_ship_engine(target="local_demo", project_path=str(app_dir))
    assert out["ok"] is True
    assert out["deployment"]["status"] == "simulated"


def test_concurrent_deploy_for_one_project_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs on one VPS pick the same colour, the same ports, and race docker rm."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    key = deploy_key(
        target="vps_ssh", project_path=str(app_dir), hostname="", vps_host="203.0.113.10"
    )
    with project_deploy_lock(key), pytest.raises(DeployInProgressError):
        run_ship_engine(
            target="vps_ssh",
            project_path=str(app_dir),
            vps_host="203.0.113.10",
        )

    # The slot is released once the first deploy is done — on failure too.
    out = run_ship_engine(
        target="vps_ssh", project_path=str(app_dir), vps_host=""
    )
    assert out["deployment"]["status"] == "blocked"
    assert not active_deploys(), "the lock must not leak after the run"


def test_a_different_project_is_not_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    first = tmp_path / "a"
    second = tmp_path / "b"
    for d in (first, second):
        d.mkdir()
        (d / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    held = deploy_key(target="local_demo", project_path=str(first), hostname="", vps_host="")
    with project_deploy_lock(held):
        out = run_ship_engine(target="local_demo", project_path=str(second))
    assert out["ok"] is True
