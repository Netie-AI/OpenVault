"""#63 migrate SITE_/password KeyVault rows into SecretStore.

Fixtures are fake. No live dumps. Loopback custody only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.airgpt_keyvault import upsert_env_secret
from openmw.openvault.vault.crypto import Seal, VaultSealedError
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.migrate_passwords import migrate_site_password_keys
from openmw.openvault.vault.precheck import SITE_PASSWORD_NOT_HOP, probe_key
from openmw.openvault.vault.secrets import SecretStore
from openmw.openvault.vault.store import KeyVault

FAKE_SITE_PW = "site-login-not-an-api-key"
FAKE_GROQ = "gsk_test_not_a_live_key_0001"
FAKE_CUSTOM_LLM = "sk-custom-llm-not-a-site-password"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    return root


@pytest.fixture()
def vault(home: Path) -> KeyVault:
    return KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))


@pytest.fixture()
def secrets(vault: KeyVault) -> SecretStore:
    return SecretStore(db_path=vault.db_path, seal=vault.seal)


def _client(vault: KeyVault, secrets: SecretStore, host: str = "127.0.0.1") -> TestClient:
    return TestClient(
        create_app(
            vault=vault,
            secrets=secrets,
            mock_health=True,
            enable_precheck_loop=False,
        ),
        client=(host, 5555),
    )


def test_migrate_moves_site_rows_and_leaves_llm_keys(
    vault: KeyVault, secrets: SecretStore
) -> None:
    site = vault.create(
        label="SITE_accounts_google_com_test",
        provider="custom",
        secret=FAKE_SITE_PW,
        role="free",
        base_url="",
    )
    groq = vault.create(
        label="Groq",
        provider="groq",
        secret=FAKE_GROQ,
        role="free",
        base_url="https://api.groq.com/openai/v1",
    )
    custom_llm = vault.create(
        label="My OpenAI-compat hop",
        provider="custom",
        secret=FAKE_CUSTOM_LLM,
        role="free",
        base_url="",
    )
    result = migrate_site_password_keys(vault, secrets)
    assert result["ok"] is True
    assert result["migrated"] == 1
    assert result["failed"] == 0
    assert result["remaining_site_keys"] == 0
    assert vault.get(site.id) is None
    assert vault.get(groq.id) is not None
    assert vault.get(custom_llm.id) is not None
    listed = secrets.list_secrets(kind="password")
    assert len(listed) == 1
    assert listed[0].label == "SITE_accounts_google_com_test"
    assert listed[0].kind == "password"
    assert FAKE_SITE_PW not in listed[0].masked
    assert secrets.reveal(listed[0].id) == FAKE_SITE_PW


def test_migrate_skips_duplicate_secret_label_and_still_deletes_hop(
    vault: KeyVault, secrets: SecretStore
) -> None:
    secrets.create_password(label="SITE_PASSWORD", password="already-stored-password")
    vault.create(
        label="SITE_PASSWORD",
        provider="custom",
        secret=FAKE_SITE_PW,
        role="free",
        base_url="",
    )
    result = migrate_site_password_keys(vault, secrets)
    assert result["ok"] is True
    assert result["migrated"] == 0
    assert result["already_in_secrets"] == 1
    assert vault.list_keys() == []
    rows = secrets.list_secrets(kind="password")
    assert len(rows) == 1
    assert secrets.reveal(rows[0].id) == "already-stored-password"


def test_migrate_refuses_when_sealed(vault: KeyVault, secrets: SecretStore) -> None:
    vault.create(
        label="SITE_PASSWORD",
        provider="custom",
        secret=FAKE_SITE_PW,
        role="free",
        base_url="",
    )
    vault.seal.lock()
    with pytest.raises(VaultSealedError):
        migrate_site_password_keys(vault, secrets)
    assert len(vault.list_keys()) == 1
    assert secrets.list_secrets(kind="password") == []


def test_migrate_api_and_upsert_refuse(vault: KeyVault, secrets: SecretStore) -> None:
    vault.create(
        label="SITE_PASSWORD",
        provider="custom",
        secret=FAKE_SITE_PW,
        role="free",
        base_url="",
    )
    client = _client(vault, secrets)
    moved = client.post("/api/vault/migrate-site-passwords")
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["migrated"] == 1
    assert body["remaining_site_keys"] == 0
    keys = client.get("/api/keys").json()["keys"]
    assert keys == []
    secret_rows = client.get("/api/secrets").json()["secrets"]
    assert len(secret_rows) == 1
    assert secret_rows[0]["kind"] == "password"
    refuse = upsert_env_secret(vault, env_key="SITE_PASSWORD", secret=FAKE_SITE_PW)
    assert refuse["ok"] is False
    assert "/api/secrets/passwords" in refuse["error"]
    lan = _client(vault, secrets, host="192.168.1.50")
    assert lan.post("/api/vault/migrate-site-passwords").status_code == 401


def test_list_keys_hides_site_password_rows(vault: KeyVault, secrets: SecretStore) -> None:
    vault.create(
        label="SITE_PASSWORD",
        provider="custom",
        secret=FAKE_SITE_PW,
        role="free",
        base_url="",
    )
    groq = vault.create(
        label="Groq",
        provider="groq",
        secret=FAKE_GROQ,
        role="free",
        base_url="https://api.groq.com/openai/v1",
    )
    client = _client(vault, secrets)
    keys = client.get("/api/keys").json()["keys"]
    assert [k["label"] for k in keys] == ["Groq"]
    assert keys[0]["id"] == groq.id
    snap = client.get("/api/keyvault/snapshot").json()
    assert snap["keys_count"] == 1
    assert all("SITE_" not in str(p.get("label") or "") for p in snap["providers"])


def test_patch_refuses_site_password_shape(vault: KeyVault, secrets: SecretStore) -> None:
    hop = vault.create(
        label="My OpenAI-compat hop",
        provider="custom",
        secret=FAKE_CUSTOM_LLM,
        role="free",
        base_url="",
    )
    client = _client(vault, secrets)
    refuse = client.patch(f"/api/keys/{hop.id}", json={"label": "SITE_PASSWORD"})
    assert refuse.status_code == 400, refuse.text
    assert "/api/secrets/passwords" in refuse.text
    left = client.get("/api/keys").json()["keys"]
    assert len(left) == 1
    assert left[0]["label"] == "My OpenAI-compat hop"


def test_precheck_skips_site_password_keeps_custom_llm_error(vault: KeyVault) -> None:
    site = vault.create(
        label="SITE_PASSWORD",
        provider="custom",
        secret=FAKE_SITE_PW,
        role="free",
        base_url="",
    )
    custom = vault.create(
        label="My OpenAI-compat hop",
        provider="custom",
        secret=FAKE_CUSTOM_LLM,
        role="free",
        base_url="",
    )
    site_res = asyncio.run(probe_key(site, FAKE_SITE_PW))
    assert site_res.error == SITE_PASSWORD_NOT_HOP
    assert site_res.status == "unknown"
    custom_res = asyncio.run(probe_key(custom, FAKE_CUSTOM_LLM))
    assert custom_res.error == "missing base_url for custom provider"
    assert custom_res.status == "error"


def test_fallback_skips_site_password_rows(vault: KeyVault) -> None:
    site = vault.create(
        label="SITE_PASSWORD",
        provider="custom",
        secret=FAKE_SITE_PW,
        role="free",
        base_url="",
    )
    groq = vault.create(
        label="Groq",
        provider="groq",
        secret=FAKE_GROQ,
        role="free",
        base_url="https://api.groq.com/openai/v1",
    )
    vault.set_precheck(site.id, status="error", latency_ms=None, error="missing base_url")
    vault.set_precheck(groq.id, status="ok", latency_ms=10.0, error=None)
    ids = [row.id for row in FallbackManager(vault).ordered_candidates()]
    assert site.id not in ids
    assert groq.id in ids
