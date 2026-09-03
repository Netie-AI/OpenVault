"""DR-0010 (c): plaintext master-key backup is visible, retirable, never silent.

Confirmation file named in DR-0010. Must land in the same commit as the
crypto.py fix so a later stale-branch merge cannot drop the bak hole while
keeping tests green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault import keywrap
from openmw.openvault.vault.crypto import (
    Seal,
    VaultCryptoError,
    _load_or_create_master_key,
    plaintext_backup_path,
)


def _client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(
        create_app(mock_health=True, enable_precheck_loop=False),
        client=(host, 5555),
    )


def _migrated_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    key_path = tmp_path / "master.key"
    key_path.write_bytes(Fernet.generate_key())
    return _client()


def test_status_reports_plaintext_backup_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _migrated_client(tmp_path, monkeypatch)
    status = client.get("/api/vault/status")
    assert status.status_code == 200
    body = status.json()
    assert "plaintext_backup_present" in body
    assert body["plaintext_backup_present"] is True
    assert plaintext_backup_path(tmp_path / "master.key").is_file()


def test_status_field_is_read_from_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-0001: deleting the file flips the field without an in-process flag."""
    client = _migrated_client(tmp_path, monkeypatch)
    bak = plaintext_backup_path(tmp_path / "master.key")
    bak.unlink()
    body = client.get("/api/vault/status").json()
    assert body["plaintext_backup_present"] is False


def test_retire_removes_bak_and_audits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _migrated_client(tmp_path, monkeypatch)
    bak = plaintext_backup_path(tmp_path / "master.key")
    res = client.post("/api/vault/backup/retire")
    assert res.status_code == 200, res.text
    assert res.json()["plaintext_backup_present"] is False
    assert not bak.is_file()
    lines = (tmp_path / "secret_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in lines]
    retired = [e for e in events if e.get("event") == "vault_backup_retired"]
    assert len(retired) == 1
    assert str(bak) in str(retired[0].get("path", ""))
    blob = (tmp_path / "secret_audit.jsonl").read_text(encoding="utf-8")
    assert Fernet.generate_key().decode() not in blob  # no accidental key dump shape


def test_retire_refuses_when_bak_does_not_match_live_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _migrated_client(tmp_path, monkeypatch)
    bak = plaintext_backup_path(tmp_path / "master.key")
    bak.write_bytes(Fernet.generate_key())
    res = client.post("/api/vault/backup/retire")
    assert res.status_code == 409, res.text
    assert bak.is_file()


def test_retire_refuses_when_unwrapped_live_key_differs_from_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If retire skipped keywrap.unwrap, swapping the live file would still shred the bak."""
    client = _migrated_client(tmp_path, monkeypatch)
    bak = plaintext_backup_path(tmp_path / "master.key")
    other = Fernet.generate_key()
    (tmp_path / "master.key").write_bytes(keywrap.wrap(other))
    res = client.post("/api/vault/backup/retire")
    assert res.status_code == 409, res.text
    assert bak.is_file()


def test_retire_refuses_while_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _migrated_client(tmp_path, monkeypatch)
    set_pp = client.post(
        "/api/vault/passphrase", json={"passphrase": "correct-horse-battery-staple"}
    )
    assert set_pp.status_code == 200, set_pp.text
    assert set_pp.json()["plaintext_backup_present"] is True
    sealed = _client()
    bak = plaintext_backup_path(tmp_path / "master.key")
    res = sealed.post("/api/vault/backup/retire")
    assert res.status_code == 403, res.text
    assert bak.is_file()


def test_retire_is_loopback_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _migrated_client(tmp_path, monkeypatch)
    remote = _client("10.0.0.8")
    bak = plaintext_backup_path(tmp_path / "master.key")
    res = remote.post("/api/vault/backup/retire")
    assert res.status_code == 403
    assert bak.is_file()


def test_keys_db_plus_bak_without_live_key_does_not_yield_plaintext(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "master.key"
    original = Fernet.generate_key()
    key_path.write_bytes(original)
    token = Seal(original).encrypt("sk-must-not-leak")
    _load_or_create_master_key(key_path)
    key_path.unlink()
    assert plaintext_backup_path(key_path).is_file()
    with pytest.raises(VaultCryptoError, match="plaintext backup"):
        Seal(key_path=key_path)
    with pytest.raises(VaultCryptoError, match="plaintext backup"):
        _load_or_create_master_key(key_path)
    # Folder load must not decrypt. Using the bak bytes as an in-memory key is
    # holding the key material, not opening the vault directory.
    with pytest.raises(VaultCryptoError):
        Seal(key_path=key_path).decrypt(token)
