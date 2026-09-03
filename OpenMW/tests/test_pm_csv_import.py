"""Official Google / Apple / Chrome CSV dump-import into sealed secrets."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal

CHROME_CSV = """name,url,username,password,note
Example,https://example.com,alice,correct-horse-battery,keep
EmptyPass,https://empty.example,bob,,
"""

APPLE_CSV = """Title,URL,Username,Password,Notes,OTPAuth
Mail,https://mail.example,ada,apple-secret,,otpauth://totp/x
"""

CARD_CSV = """name,card_number,exp_month,exp_year,cardholder,cvv
Personal Visa,4242424242424242,11,2029,J HONG,123
"""


def _client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(
        create_app(mock_health=True, enable_precheck_loop=False),
        client=(host, 5555),
    )


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_dry_run_default_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    csv_path = _write(tmp_path / "chrome.csv", CHROME_CSV)
    client = _client()
    res = client.post("/api/vault/ingest-pm", json={"path": str(csv_path)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dry_run"] is True
    assert body["imported"] == 0
    assert body["scanned"] == 2
    listed = client.get("/api/secrets").json()["secrets"]
    assert listed == []
    blob = res.text
    assert "correct-horse-battery" not in blob


def test_write_imports_passwords_and_skips_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    csv_path = _write(tmp_path / "chrome.csv", CHROME_CSV)
    client = _client()
    res = client.post(
        "/api/vault/ingest-pm", json={"path": str(csv_path), "dry_run": False}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["imported"] == 1
    secrets = client.get("/api/secrets").json()["secrets"]
    assert len(secrets) == 1
    row = secrets[0]
    assert row["kind"] == "password"
    assert row["username"] == "alice"
    assert row["url"] == "https://example.com"
    assert "correct-horse-battery" not in str(row)
    assert "correct-horse-battery" not in (tmp_path / "keys.db").read_bytes().decode(
        "latin1"
    )


def test_cvv_is_stripped_and_not_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    csv_path = _write(tmp_path / "cards.csv", CARD_CSV)
    client = _client()
    res = client.post(
        "/api/vault/ingest-pm", json={"path": str(csv_path), "dry_run": False}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["cvv_stripped"] == 1
    assert body["imported"] == 1
    assert "123" not in res.text
    secrets = client.get("/api/secrets").json()["secrets"]
    assert secrets[0]["kind"] == "payment_card"
    assert secrets[0]["last4"] == "4242"
    db = (tmp_path / "keys.db").read_bytes()
    assert b"123" not in db
    assert b"4242424242424242" not in db


def test_apple_header_dialect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    csv_path = _write(tmp_path / "apple.csv", APPLE_CSV)
    client = _client()
    res = client.post(
        "/api/vault/ingest-pm", json={"path": str(csv_path), "dry_run": False}
    )
    assert res.status_code == 200, res.text
    assert res.json()["imported"] == 1
    row = client.get("/api/secrets").json()["secrets"][0]
    assert row["label"] == "Mail"
    assert row["username"] == "ada"


def test_sealed_write_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    csv_path = _write(tmp_path / "chrome.csv", CHROME_CSV)
    first = _client()
    set_pp = first.post(
        "/api/vault/passphrase", json={"passphrase": "correct-horse-battery-staple"}
    )
    assert set_pp.status_code == 200, set_pp.text
    sealed = _client()
    res = sealed.post(
        "/api/vault/ingest-pm", json={"path": str(csv_path), "dry_run": False}
    )
    assert res.status_code == 403
    dry = sealed.post("/api/vault/ingest-pm", json={"path": str(csv_path)})
    assert dry.status_code == 200
    assert dry.json()["imported"] == 0


def test_non_loopback_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    csv_path = _write(tmp_path / "chrome.csv", CHROME_CSV)
    _client()
    remote = _client("10.0.0.8")
    res = remote.post(
        "/api/vault/ingest-pm", json={"path": str(csv_path), "dry_run": False}
    )
    assert res.status_code == 403


def test_refuses_write_while_plaintext_backup_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    key_path = tmp_path / "master.key"
    raw = Fernet.generate_key()
    key_path.write_bytes(raw)
    Seal(key_path=key_path)  # migrate -> bak
    csv_path = _write(tmp_path / "chrome.csv", CHROME_CSV)
    client = _client()
    assert client.get("/api/vault/status").json()["plaintext_backup_present"] is True
    res = client.post(
        "/api/vault/ingest-pm", json={"path": str(csv_path), "dry_run": False}
    )
    assert res.status_code == 409, res.text
    assert client.get("/api/secrets").json()["secrets"] == []


def test_duplicate_skipped_on_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    csv_path = _write(tmp_path / "chrome.csv", CHROME_CSV)
    client = _client()
    first = client.post(
        "/api/vault/ingest-pm", json={"path": str(csv_path), "dry_run": False}
    )
    second = client.post(
        "/api/vault/ingest-pm", json={"path": str(csv_path), "dry_run": False}
    )
    assert first.json()["imported"] == 1
    assert second.json()["imported"] == 0
    actions = [r["action"] for r in second.json()["files"][0]["results"]]
    assert "skipped_duplicate" in actions
    assert len(client.get("/api/secrets").json()["secrets"]) == 1
