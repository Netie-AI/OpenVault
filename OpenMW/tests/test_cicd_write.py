"""Write the OpenVault ship workflow; never invent a preview URL."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.cicd import SUGGESTED_WORKFLOW_REL, write_suggested_workflow
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


def test_write_creates_workflow_without_a_url(tmp_path: Path) -> None:
    project = tmp_path / "app"
    project.mkdir()
    out = write_suggested_workflow(project)
    assert out["ok"] is True
    assert out["written"] is True
    assert out["public_url"] is None
    assert out["git_push"] == "workflow-on-disk"
    path = project / SUGGESTED_WORKFLOW_REL
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "not invent" in text.lower() or "Refuse to invent" in text
    assert 'default: "true"' in text


def test_write_refuses_overwrite_unless_asked(tmp_path: Path) -> None:
    project = tmp_path / "app"
    project.mkdir()
    first = write_suggested_workflow(project)
    dest = project / SUGGESTED_WORKFLOW_REL
    dest.write_text("name: custom\n", encoding="utf-8")
    second = write_suggested_workflow(project)
    assert second["written"] is False
    assert second["already_present"] is True
    assert dest.read_text(encoding="utf-8") == "name: custom\n"
    third = write_suggested_workflow(project, overwrite=True)
    assert third["written"] is True
    assert "openvault-ship" in dest.read_text(encoding="utf-8")


def test_write_api_is_loopback_and_url_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    project = tmp_path / "site"
    project.mkdir()
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=Seal(Fernet.generate_key()))
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    client = TestClient(app, client=("127.0.0.1", 5555))
    res = client.post("/api/ship/cicd/write", json={"project_path": str(project)})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["public_url"] is None
    assert (project / SUGGESTED_WORKFLOW_REL).is_file()
