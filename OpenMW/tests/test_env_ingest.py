"""Auto-vault: importing provider secrets from the environment."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.env_ingest import ingest_environment, scan_environment
from openmw.openvault.vault.store import KeyVault

REAL_SECRET = "sk-live-abcdef123456789"


@pytest.fixture()
def vault(tmp_path: Path) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "openvault" / "keys.db", seal=seal)


def test_scan_finds_known_provider_keys() -> None:
    env = {"OPENAI_API_KEY": REAL_SECRET, "GROQ_API_KEY": "gsk_abcdef123456789"}
    found = {c.env_key: c for c in scan_environment(env)}
    assert set(found) == {"OPENAI_API_KEY", "GROQ_API_KEY"}
    assert found["OPENAI_API_KEY"].provider == "openai"
    assert found["GROQ_API_KEY"].provider == "groq"
    # never leaks the raw value
    assert REAL_SECRET not in found["OPENAI_API_KEY"].masked
    assert found["OPENAI_API_KEY"].known is True


def test_scan_skips_placeholders_and_short_values() -> None:
    env = {
        "OPENAI_API_KEY": "changeme",
        "ANTHROPIC_API_KEY": "your-key-here",
        "GROQ_API_KEY": "abc",  # too short
        "DEEPSEEK_API_KEY": "   ",
        "GOOGLE_API_KEY": "sk-a…xyz",  # already masked
    }
    assert scan_environment(env) == []


def test_scan_skips_non_secret_config() -> None:
    env = {"OPENVAULT_URL": "http://127.0.0.1:5000", "OPENAI_API_KEY": REAL_SECRET}
    keys = [c.env_key for c in scan_environment(env)]
    assert keys == ["OPENAI_API_KEY"]


def test_scan_include_unknown_opt_in() -> None:
    env = {"ACME_API_KEY": "acme-abcdef123456"}
    assert scan_environment(env) == []
    found = scan_environment(env, include_unknown=True)
    assert [c.env_key for c in found] == ["ACME_API_KEY"]
    assert found[0].known is False
    assert found[0].provider == "custom"


def test_ingest_dry_run_does_not_write(vault: KeyVault) -> None:
    env = {"OPENAI_API_KEY": REAL_SECRET}
    report = ingest_environment(vault, env=env)  # dry_run defaults True
    assert report["dry_run"] is True
    assert report["scanned"] == 1
    assert report["imported"] == 0
    assert report["results"][0]["action"] == "would_import"
    assert vault.list_keys() == []


def test_ingest_writes_encrypted_and_never_echoes_secret(vault: KeyVault) -> None:
    env = {"OPENAI_API_KEY": REAL_SECRET}
    report = ingest_environment(vault, env=env, dry_run=False)
    assert report["imported"] == 1
    row = report["results"][0]
    assert row["ok"] is True
    assert row["action"] in ("created", "updated")
    # the raw secret must not appear anywhere in the report
    assert REAL_SECRET not in json.dumps(report)
    # but it is retrievable from the vault, decrypted
    keys = vault.list_keys()
    assert len(keys) == 1
    assert vault.get_secret(keys[0].id) == REAL_SECRET
    assert keys[0].provider == "openai"


def test_env_scan_endpoint(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", REAL_SECRET)
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    client = TestClient(app)

    scan = client.get("/api/vault/env-scan")
    assert scan.status_code == 200
    body = scan.json()
    assert body["ok"] is True
    assert "OPENAI_API_KEY" in [c["env_key"] for c in body["candidates"]]
    assert REAL_SECRET not in scan.text

    # dry-run ingest reports without writing
    ingest = client.post("/api/vault/ingest-env", json={"dry_run": True})
    assert ingest.status_code == 200
    assert ingest.json()["dry_run"] is True
    assert vault.list_keys() == []
