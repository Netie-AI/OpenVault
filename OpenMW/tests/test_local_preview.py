"""Loopback preview is not a live host URL."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.engine import run_ship_engine
from openmw.openvault.ship.local_preview import start_local_preview, stop_all_previews
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


@pytest.fixture(autouse=True)
def _stop_previews():
    yield
    stop_all_previews()


def test_start_serves_index_on_loopback_only(tmp_path: Path) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "index.html").write_text("hello-preview", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    url = start_local_preview(root)
    assert url.startswith("http://127.0.0.1:")
    assert "https://" not in url
    with httpx.Client(timeout=5.0) as client:
        ok = client.get(url + "/")
        assert ok.status_code == 200
        assert "hello-preview" in ok.text
        hidden = client.get(url + "/.env")
        assert hidden.status_code == 404
        assert "SECRET" not in hidden.text


def test_empty_dir_does_not_invent_a_url(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert start_local_preview(empty) == ""


def test_engine_local_preview_is_not_public_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ov"))
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>local</h1>", encoding="utf-8")
    out = run_ship_engine(target="local_demo", project_path=str(site), hostname="")
    dep = out["deployment"]
    assert dep["public_url"] == ""
    assert dep["local_preview_url"].startswith("http://127.0.0.1:")
    with httpx.Client(timeout=5.0) as client:
        resp = client.get(dep["local_preview_url"] + "/")
    assert resp.status_code == 200
    assert "local" in resp.text


def _preview_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "ovhome"))
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=seal)
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def test_preview_api_is_not_a_live_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>preview-api</h1>", encoding="utf-8")
    (site / ".env").write_text("SECRET=no", encoding="utf-8")
    client = _preview_client(tmp_path, monkeypatch)
    res = client.post("/api/ship/preview", json={"project_path": str(site)})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["public_url"] is None
    assert body["live"] is False
    assert body["live_public_url"] is None
    url = body["local_preview_url"]
    assert url.startswith("http://127.0.0.1:")
    with httpx.Client(timeout=5.0) as http:
        page = http.get(url + "/")
        hidden = http.get(url + "/.env")
    assert page.status_code == 200
    assert "preview-api" in page.text
    assert hidden.status_code == 404
    assert "SECRET" not in hidden.text


def test_preview_api_empty_dir_refuses_without_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    client = _preview_client(tmp_path, monkeypatch)
    res = client.post("/api/ship/preview", json={"project_path": str(empty)})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["local_preview_url"] == ""
    assert body["public_url"] is None
    assert body["live"] is False
