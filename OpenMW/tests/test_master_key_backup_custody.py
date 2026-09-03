"""DR-0010 option (c): verify-then-retire master.key.v0.bak.

Confirmation lives in this file with the fix (DR merge-order guard). The bak
stays at migrate; status reports it; retire unwraps the live wrap and
byte-compares before delete; a folder of keys.db + bak without a live wrapped
key must not yield plaintext.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import (
    Seal,
    VaultCryptoError,
    _load_or_create_master_key,
    plaintext_backup_path,
)
from openmw.openvault.vault.secrets import SecretStore
from openmw.openvault.vault.store import KeyVault

REVEAL_HEADER = {"X-OpenVault-Reveal": "intentional"}
SECRET = "sk-live-backup-custody"


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    return TestClient(
        create_app(mock_health=True, enable_precheck_loop=False),
        client=("127.0.0.1", 5555),
    )


def _migrate(tmp_path: Path) -> Path:
    key_path = tmp_path / "master.key"
    original = Fernet.generate_key()
    key_path.write_bytes(original)
    loaded = _load_or_create_master_key(key_path)
    assert loaded == original
    return key_path


def test_migration_leaves_bak_and_status_reports_present(tmp_path: Path) -> None:
    key_path = _migrate(tmp_path)
    bak = plaintext_backup_path(key_path)
    assert bak.is_file(), "migration must leave a backup"
    status = Seal(key_path=key_path).status()
    assert status["plaintext_backup_present"] is True
    assert status["sealed"] is False


def test_status_reports_bak_even_when_unsealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-0011: warning must not be gated on sealed (SecretsPanel CSS trap)."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    _migrate(tmp_path)
    client = _client(tmp_path, monkeypatch)
    body = client.get("/api/vault/status").json()
    assert body["sealed"] is False
    assert body["plaintext_backup_present"] is True


def test_set_passphrase_warns_but_does_not_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    _migrate(tmp_path)
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/vault/passphrase", json={"passphrase": "a-long-enough-phrase"})
    assert res.status_code == 200
    assert res.json()["passphrase_configured"] is True
    assert res.json()["plaintext_backup_present"] is True
    assert plaintext_backup_path(tmp_path / "master.key").is_file()


def test_retire_verifies_then_deletes_bak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    _migrate(tmp_path)
    client = _client(tmp_path, monkeypatch)
    bak = tmp_path / "master.key.v0.bak"
    assert bak.is_file()

    res = client.post("/api/vault/backup/retire", json={})
    assert res.status_code == 200, res.text
    assert res.json()["plaintext_backup_present"] is False
    assert not bak.exists()

    audit = tmp_path / "secret_audit.jsonl"
    assert audit.is_file()
    lines = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines() if line]
    assert any(row.get("event") == "vault_backup_retired" for row in lines)


def test_retire_refuses_when_bak_does_not_match_live_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    _migrate(tmp_path)
    bak = tmp_path / "master.key.v0.bak"
    bak.write_bytes(Fernet.generate_key())
    client = _client(tmp_path, monkeypatch)

    res = client.post("/api/vault/backup/retire", json={})
    assert res.status_code == 400
    assert "does not match" in res.json()["detail"]
    assert bak.is_file(), "mismatch must not delete the bak"


def test_retire_is_loopback_and_unsealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    _migrate(tmp_path)
    remote = TestClient(
        create_app(mock_health=True, enable_precheck_loop=False),
        client=("192.168.1.50", 5555),
    )
    denied = remote.post("/api/vault/backup/retire", json={})
    assert denied.status_code == 403

    first = _client(tmp_path, monkeypatch)
    first.post("/api/vault/passphrase", json={"passphrase": "a-long-enough-phrase"})
    first.post("/api/vault/lock")
    sealed = first.post("/api/vault/backup/retire", json={})
    assert sealed.status_code == 403
    assert "sealed" in sealed.json()["detail"].lower()


def test_copy_open_of_bak_plus_db_does_not_yield_plaintext(tmp_path: Path) -> None:
    """keys.db + master.key.v0.bak and no live wrapped key must fail closed."""
    live = tmp_path / "live"
    live.mkdir()
    key_path = live / "master.key"
    original = Fernet.generate_key()
    key_path.write_bytes(original)
    _load_or_create_master_key(key_path)

    seal = Seal(key_path=key_path)
    vault = KeyVault(db_path=live / "keys.db", seal=seal)
    rec = vault.create(label="live-key", provider="openai", secret=SECRET)
    assert vault.get_secret(rec.id) == SECRET

    stolen = tmp_path / "stolen"
    stolen.mkdir()
    (stolen / "keys.db").write_bytes((live / "keys.db").read_bytes())
    (stolen / "master.key.v0.bak").write_bytes((live / "master.key.v0.bak").read_bytes())
    # Deliberately no master.key — bak must not be promoted to one.
    assert not (stolen / "master.key").exists()

    opened = Seal(key_path=stolen / "master.key")
    thief = KeyVault(db_path=stolen / "keys.db", seal=opened)
    try:
        leaked = thief.get_secret(rec.id)
    except VaultCryptoError:
        leaked = None
    assert leaked != SECRET
    assert leaked in (None, "")
    # New key file may be created for an empty vault, but it must not be the bak.
    if (stolen / "master.key").is_file():
        assert (stolen / "master.key").read_bytes() != (stolen / "master.key.v0.bak").read_bytes()


def test_retire_missing_bak_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/vault/backup/retire", json={})
    assert res.status_code == 400
    assert "no plaintext backup" in res.json()["detail"]


def test_passwords_store_survives_retire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    _migrate(tmp_path)
    seal = Seal(key_path=tmp_path / "master.key")
    secrets = SecretStore(db_path=tmp_path / "keys.db", seal=seal)
    row = secrets.create_password(label="site", password="correct-horse-battery")
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/vault/backup/retire", json={}).status_code == 200
    revealed = client.get(f"/api/secrets/{row.id}/reveal", headers=REVEAL_HEADER)
    assert revealed.status_code == 200
    assert revealed.json()["secret"] == "correct-horse-battery"
