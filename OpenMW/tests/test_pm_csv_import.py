"""Official Google / Apple / Chrome CSV dump-import (#38).

Synthetic fixtures only. Dry-run default writes nothing. CVV columns are
stripped with an explicit reason. Sealed vault fails closed on non-dry-run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import _load_or_create_master_key
from openmw.openvault.vault.pm_import import parse_csv_text

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VISA_PAN = "4242424242424242"


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    return TestClient(
        create_app(mock_health=True, enable_precheck_loop=False),
        client=("127.0.0.1", 5555),
    )


def test_detects_three_official_dialects() -> None:
    google = (FIXTURES / "pm_google.csv").read_text(encoding="utf-8")
    apple = (FIXTURES / "pm_apple.csv").read_text(encoding="utf-8")
    chrome = (FIXTURES / "pm_chrome.csv").read_text(encoding="utf-8")
    g_dialect, _g_rows = parse_csv_text(google)
    a_dialect, _a_rows = parse_csv_text(apple)
    c_dialect, _c_rows = parse_csv_text(chrome)
    assert g_dialect == "google"
    assert a_dialect == "apple"
    assert c_dialect == "chrome"


def test_dry_run_default_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    csv_text = (FIXTURES / "pm_google.csv").read_text(encoding="utf-8")
    res = client.post("/api/vault/ingest-pm", json={"csv_text": csv_text})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dry_run"] is True
    assert body["imported"] == 0
    assert body["scanned"] == 3
    would = [r for r in body["results"] if r["action"] == "would_import"]
    skipped = [r for r in body["results"] if r["action"] == "skipped"]
    assert len(would) == 2
    assert len(skipped) == 1
    assert client.get("/api/secrets").json()["secrets"] == []
    assert "horse-battery-1" not in res.text


def test_google_and_chrome_import_when_unsealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    google = (FIXTURES / "pm_google.csv").read_text(encoding="utf-8")
    chrome = (FIXTURES / "pm_chrome.csv").read_text(encoding="utf-8")
    g = client.post("/api/vault/ingest-pm", json={"csv_text": google, "dry_run": False})
    c = client.post("/api/vault/ingest-pm", json={"csv_text": chrome, "dry_run": False})
    assert g.status_code == 200, g.text
    assert c.status_code == 200, c.text
    assert g.json()["imported"] == 2
    assert g.json()["skipped"] == 1
    assert c.json()["imported"] == 1
    listed = client.get("/api/secrets", params={"kind": "password"}).json()["secrets"]
    labels = {row["label"] for row in listed}
    assert {"GitHub", "Netie", "chrome.google.com"} <= labels
    assert "horse-battery-1" not in json.dumps(listed)


def test_apple_csv_round_trips_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    apple = (FIXTURES / "pm_apple.csv").read_text(encoding="utf-8")
    res = client.post("/api/vault/ingest-pm", json={"csv_text": apple, "dry_run": False})
    assert res.status_code == 200, res.text
    row = next(s for s in client.get("/api/secrets").json()["secrets"] if s["label"] == "iCloud")
    revealed = client.get(
        f"/api/secrets/{row['id']}/reveal",
        headers={"X-OpenVault-Reveal": "intentional"},
    )
    assert revealed.status_code == 200
    assert revealed.json()["secret"] == "apple-secret-9"


def test_cvv_column_is_stripped_and_never_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    csv_text = (FIXTURES / "pm_card_cvv.csv").read_text(encoding="utf-8")
    res = client.post("/api/vault/ingest-pm", json={"csv_text": csv_text, "dry_run": False})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["cvv_stripped"] == 1
    assert body["imported"] == 1
    assert any("CVV" in (r.get("reason") or "") for r in body["results"])
    assert "737" not in res.text
    listed = client.get("/api/secrets").json()["secrets"]
    assert listed[0]["kind"] == "payment_card"
    assert listed[0]["last4"] == "4242"
    raw = (tmp_path / "keys.db").read_bytes()
    assert b"737" not in raw
    assert VISA_PAN.encode() not in raw


def test_import_dir_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    dest = tmp_path / "import"
    dest.mkdir()
    dest.joinpath("google.csv").write_text(
        (FIXTURES / "pm_google.csv").read_text(encoding="utf-8"), encoding="utf-8"
    )
    dry = client.post("/api/vault/ingest-pm", json={"scan_import_dir": True})
    assert dry.status_code == 200
    assert dry.json()["dry_run"] is True
    assert dry.json()["imported"] == 0
    assert client.get("/api/secrets").json()["secrets"] == []

    wrote = client.post("/api/vault/ingest-pm", json={"scan_import_dir": True, "dry_run": False})
    assert wrote.status_code == 200
    assert wrote.json()["imported"] == 2


def test_sealed_vault_fails_closed_on_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    _load_or_create_master_key(tmp_path / "master.key")
    first = _client(tmp_path, monkeypatch)
    first.post("/api/vault/passphrase", json={"passphrase": "a-long-enough-phrase"})
    first.post("/api/vault/lock")
    csv_text = (FIXTURES / "pm_chrome.csv").read_text(encoding="utf-8")
    res = first.post("/api/vault/ingest-pm", json={"csv_text": csv_text, "dry_run": False})
    assert res.status_code == 403
    assert "sealed" in res.json()["detail"].lower()
    assert first.get("/api/secrets").json()["secrets"] == []


def test_non_loopback_ingest_is_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    remote = TestClient(
        create_app(mock_health=True, enable_precheck_loop=False),
        client=("10.0.0.8", 5555),
    )
    res = remote.post("/api/vault/ingest-pm", json={"csv_text": "name,url,username,password\n"})
    assert res.status_code == 403
