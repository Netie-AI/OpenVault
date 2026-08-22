"""Ship engine SSE stream contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.engine import EngineStep, ShipDeployment, save_deployment
from openmw.openvault.ship.stream import deployment_frames
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KeyVault:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("OPENVAULT_HOME", str(home))
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=home / "keys.db", seal=seal)


def test_deployment_frames_emit_complete_with_success() -> None:
    dep = ShipDeployment(
        deployment_id="abc123",
        target="local_demo",
        project_path="D:/tmp/app",
        hostname="",
        steps=[
            EngineStep("source", "Resolve folder", "pass", "D:/tmp/app"),
            EngineStep("detect", "Stack detection", "pass", "node"),
            EngineStep("host", "Local demo", "pass", "simulated"),
        ],
        ready=True,
        public_url="http://127.0.0.1:9/",
    )
    frames = deployment_frames(dep)
    joined = "".join(frames)
    assert '"type":"connected"' in joined or '"type": "connected"' in joined.replace(" ", "")
    assert "complete" in joined
    assert '"success":true' in joined.replace(" ", "")
    assert "end" in joined


def test_ship_engine_stream_endpoint(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home2"))
    dep = ShipDeployment(
        deployment_id="streamtest01",
        target="local_demo",
        project_path="D:/tmp/app",
        hostname="",
        steps=[EngineStep("source", "Resolve", "pass", "ok")],
        ready=True,
    )
    save_deployment(dep)
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    client = TestClient(app, client=("127.0.0.1", 5555))
    with client.stream("GET", f"/api/ship/engine/{dep.deployment_id}/stream") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in (resp.headers.get("content-type") or "")
        body = b"".join(resp.iter_bytes()).decode("utf-8", errors="replace")
        assert "complete" in body
        assert "success" in body
