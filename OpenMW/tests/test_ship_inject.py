"""Secrets-at-ship inject (OpenVault#28).

OpenVault resolves vaulted secrets into deploy env; sealed/missing refuse
loudly; plaintext never appears in API payloads or scrubbed dumps.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.inject import (
    InjectError,
    ShipEnvRef,
    escape_systemd_env_value,
    redact_plaintext,
    resolve_ship_env,
    scrub_mapping,
    systemd_environment_line,
)
from openmw.openvault.vault.accounts import AccountStore
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.secrets import SecretStore
from openmw.openvault.vault.store import KeyVault

SECRET_VALUE = "sk-inject-plaintext-NEVER-ECHO-me"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.setenv("OPENSHIP_MODE", "simulate")
    return root


@pytest.fixture()
def vault_bundle(home: Path) -> tuple[Seal, KeyVault, SecretStore]:
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=home / "keys.db", seal=seal)
    secrets = SecretStore(db_path=home / "secrets.db", seal=seal)
    return seal, vault, secrets


def test_resolve_key_inject_success(vault_bundle: tuple[Seal, KeyVault, SecretStore]) -> None:
    _seal, vault, secrets = vault_bundle
    rec = vault.create(
        label="ship-key",
        provider="openai",
        secret=SECRET_VALUE,
        role="free",
    )
    result = resolve_ship_env(
        [ShipEnvRef(env_name="OPENAI_API_KEY", key_id=rec.id)],
        vault=vault,
        secrets=secrets,
    )
    assert result.env == {"OPENAI_API_KEY": SECRET_VALUE}
    assert result.names == ["OPENAI_API_KEY"]
    summary = result.summary()
    assert summary["count"] == 1
    assert SECRET_VALUE not in str(summary)


def test_resolve_password_inject_success(
    vault_bundle: tuple[Seal, KeyVault, SecretStore],
) -> None:
    _seal, vault, secrets = vault_bundle
    pw = secrets.create_password(label="db", password="db-pass-xyz-99")
    result = resolve_ship_env(
        [ShipEnvRef(env_name="DB_PASSWORD", secret_id=pw.id)],
        vault=vault,
        secrets=secrets,
    )
    assert result.env["DB_PASSWORD"] == "db-pass-xyz-99"
    assert result.sources[0]["source"] == "secret"


def test_resolve_sealed_refuses(vault_bundle: tuple[Seal, KeyVault, SecretStore]) -> None:
    seal, vault, secrets = vault_bundle
    rec = vault.create(
        label="k",
        provider="openai",
        secret=SECRET_VALUE,
        role="free",
    )
    seal.lock()
    assert seal.is_sealed
    with pytest.raises(InjectError) as excinfo:
        resolve_ship_env(
            [ShipEnvRef(env_name="OPENAI_API_KEY", key_id=rec.id)],
            vault=vault,
            secrets=secrets,
        )
    assert "sealed" in excinfo.value.reason.lower()
    assert excinfo.value.status_code == 403


def test_resolve_missing_key_refuses(
    vault_bundle: tuple[Seal, KeyVault, SecretStore],
) -> None:
    _seal, vault, secrets = vault_bundle
    with pytest.raises(InjectError) as excinfo:
        resolve_ship_env(
            [ShipEnvRef(env_name="MISSING", key_id="deadbeef")],
            vault=vault,
            secrets=secrets,
        )
    assert "not found" in excinfo.value.reason.lower()


def test_resolve_payment_card_refuses(
    vault_bundle: tuple[Seal, KeyVault, SecretStore],
) -> None:
    _seal, vault, secrets = vault_bundle
    card = secrets.create_card(
        label="corp",
        pan="4111111111111111",
        exp_month=12,
        exp_year=2030,
    )
    with pytest.raises(InjectError) as excinfo:
        resolve_ship_env(
            [ShipEnvRef(env_name="CARD_PAN", secret_id=card.id)],
            vault=vault,
            secrets=secrets,
        )
    assert "payment card" in excinfo.value.reason.lower() or "pci" in excinfo.value.reason.lower()


def test_scrub_and_redact_omit_plaintext() -> None:
    payload = {
        "ok": True,
        "detail": f"used {SECRET_VALUE} in deploy",
        "envVars": {"OPENAI_API_KEY": SECRET_VALUE},
        "nested": [{"msg": SECRET_VALUE}],
    }
    scrubbed = scrub_mapping(payload, [SECRET_VALUE])
    assert scrubbed["envVars"] == "[omitted]"
    assert SECRET_VALUE not in str(scrubbed)
    assert "[redacted]" in scrubbed["detail"]
    assert redact_plaintext(SECRET_VALUE, [SECRET_VALUE]) == "[redacted]"


def test_systemd_env_quoting() -> None:
    assert escape_systemd_env_value('a\\b"c%d\ne') == 'a\\\\b\\"c%%d\\ne'
    line = systemd_environment_line("FOO_BAR", 'x"y')
    assert line == 'Environment="FOO_BAR=x\\"y"'
    with pytest.raises(InjectError):
        systemd_environment_line("bad-name!", "v")


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "web"
    project.mkdir()
    (project / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    return project


def test_freebuild_execute_injects_without_echoing_plaintext(
    home: Path, tmp_path: Path
) -> None:
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=home / "keys.db", seal=seal)
    secrets = SecretStore(db_path=home / "secrets.db", seal=seal)
    accounts = AccountStore(db_path=home / "accounts.db")
    app = create_app(
        vault=vault,
        accounts=accounts,
        secrets=secrets,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    client = TestClient(app, client=("127.0.0.1", 5555))

    created = client.post(
        "/api/keys",
        json={
            "label": "groq",
            "provider": "groq",
            "secret": SECRET_VALUE,
            "role": "free",
        },
    )
    assert created.status_code == 200, created.text
    key_id = created.json()["id"]

    project = _project(tmp_path)
    planned = client.post(
        "/api/freebuild/plan",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "execute": False,
        },
    )
    assert planned.status_code == 200, planned.text
    ship_id = planned.json()["ship_id"]

    exe = client.post(
        f"/api/freebuild/{ship_id}/execute",
        json={
            "simulate": True,
            "secrets": [{"env_name": "GROQ_API_KEY", "key_id": key_id}],
        },
    )
    assert exe.status_code == 200, exe.text
    body = exe.json()
    assert SECRET_VALUE not in exe.text
    assert body["secrets_injected"]["count"] == 1
    assert body["secrets_injected"]["names"] == ["GROQ_API_KEY"]
    assert "adapter" in body
    assert body["adapter"].get("secrets_injected") == ["GROQ_API_KEY"]


def test_freebuild_execute_sealed_refuses_inject(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(home))
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=home / "keys.db", seal=seal)
    secrets = SecretStore(db_path=home / "secrets.db", seal=seal)
    accounts = AccountStore(db_path=home / "accounts.db")

    rec = vault.create(
        label="groq",
        provider="groq",
        secret=SECRET_VALUE,
        role="free",
    )
    seal.set_passphrase("correct-horse-battery-staple")
    seal.lock()
    assert seal.is_sealed

    app = create_app(
        vault=vault,
        accounts=accounts,
        secrets=secrets,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    client = TestClient(app, client=("127.0.0.1", 5555))

    project = _project(tmp_path)
    # Plan does not need unseal; execute with secrets must refuse sealed vault.
    # Gate also refuses sealed/empty-ready — seed happens before lock; after lock
    # leave-gate may fire first. Unseal for plan+gate path then re-lock before inject?
    # Simpler: call resolve path via execute after unseal for gate, then lock again
    # is hard mid-request. Unit test already covers sealed resolve; HTTP path:
    # missing key is clearer. For sealed: unseal, plan, lock via vault lock API, execute.
    unseal = client.post(
        "/api/vault/unseal",
        json={"passphrase": "correct-horse-battery-staple"},
    )
    assert unseal.status_code == 200, unseal.text

    planned = client.post(
        "/api/freebuild/plan",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "execute": False,
        },
    )
    assert planned.status_code == 200, planned.text
    ship_id = planned.json()["ship_id"]

    locked = client.post("/api/vault/lock")
    assert locked.status_code == 200, locked.text
    assert client.get("/api/vault/status").json()["sealed"] is True

    exe = client.post(
        f"/api/freebuild/{ship_id}/execute",
        json={
            "simulate": True,
            "secrets": [{"env_name": "GROQ_API_KEY", "key_id": rec.id}],
        },
    )
    # Gate may 403 first (keys not ready while sealed) OR inject 403 sealed.
    assert exe.status_code == 403, exe.text
    detail = exe.json()["detail"]
    detail_s = detail if isinstance(detail, str) else str(detail)
    assert "sealed" in detail_s.lower() or (
        isinstance(detail, dict) and detail.get("allowed") is False
    )
    assert SECRET_VALUE not in exe.text


def test_freebuild_execute_missing_secret_refuses(
    home: Path, tmp_path: Path
) -> None:
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=home / "keys.db", seal=seal)
    secrets = SecretStore(db_path=home / "secrets.db", seal=seal)
    accounts = AccountStore(db_path=home / "accounts.db")
    app = create_app(
        vault=vault,
        accounts=accounts,
        secrets=secrets,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    client = TestClient(app, client=("127.0.0.1", 5555))

    # Gate needs at least one active key.
    seeded = client.post(
        "/api/keys",
        json={
            "label": "gate-key",
            "provider": "groq",
            "secret": "gsk-gate-only-not-injected",
            "role": "free",
        },
    )
    assert seeded.status_code == 200, seeded.text

    project = _project(tmp_path)
    planned = client.post(
        "/api/freebuild/plan",
        json={
            "project_path": str(project),
            "subdomain": "app.example.com",
            "execute": False,
        },
    )
    assert planned.status_code == 200
    ship_id = planned.json()["ship_id"]

    exe = client.post(
        f"/api/freebuild/{ship_id}/execute",
        json={
            "simulate": True,
            "secrets": [{"env_name": "MISSING_KEY", "key_id": "no-such-id"}],
        },
    )
    assert exe.status_code == 400, exe.text
    assert "not found" in exe.json()["detail"].lower()
    assert "sk-inject" not in exe.text
