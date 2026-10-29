"""Host adapters take credentials from the vault-backed ship env."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from openmw.openvault.ship.engine import run_ship_engine
from openmw.openvault.ship.hosts.base import DeployResult
from openmw.openvault.ship.inject import vault_host_env
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


def test_vault_host_env_matches_labels_only(tmp_path: Path) -> None:
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=seal)
    vault.create(
        label="CLOUDFLARE_API_TOKEN",
        provider="custom",
        secret="cf-token-not-a-real-secret",
        role="primary",
    )
    vault.create(
        label="GROQ_API_KEY",
        provider="groq",
        secret="gsk-not-a-host-credential",
        role="primary",
    )
    env = vault_host_env(vault)
    assert env["CLOUDFLARE_API_TOKEN"] == "cf-token-not-a-real-secret"
    assert "GROQ_API_KEY" not in env


def test_cloudflare_deploy_uses_ship_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    project = tmp_path / "light"
    project.mkdir()
    (project / "index.html").write_text("<h1>light</h1>", encoding="utf-8")
    token = "cf-token-not-a-real-secret"
    account = "acct-123"
    seen: dict[str, str] = {}

    def fake_deploy(self: object, artifact_dir: Path, *, project: str) -> DeployResult:
        seen["token"] = getattr(self, "_token")
        seen["account"] = getattr(self, "_account")
        assert (artifact_dir / "index.html").is_file()
        return DeployResult(ok=True, url="https://light.pages.dev", detail="uploaded")

    monkeypatch.setattr(
        "openmw.openvault.ship.hosts.cloudflare_pages.CloudflarePagesAdapter.deploy",
        fake_deploy,
    )
    out = run_ship_engine(
        target="cloudflare_pages",
        project_path=str(project),
        run_build=True,
        ship_env={"CLOUDFLARE_API_TOKEN": token, "CLOUDFLARE_ACCOUNT_ID": account},
    )
    assert seen["token"] == token
    assert seen["account"] == account
    assert out["deployment"]["public_url"] == "https://light.pages.dev"
    assert token not in json.dumps(out)


def test_host_credentials_are_not_forwarded_as_app_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "light"
    project.mkdir()
    (project / "index.html").write_text("<h1>light</h1>", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_deploy(
        self: object, artifact_dir: Path, *, project: str, env: object = None
    ) -> DeployResult:
        seen["host"] = getattr(self, "_host")
        seen["env"] = dict(env or {})  # type: ignore[call-overload]
        return DeployResult(ok=True, url="https://light.example", detail="uploaded")

    monkeypatch.setattr(
        "openmw.openvault.ship.hosts.spaceship_ftp.SpaceshipFtpAdapter.deploy",
        fake_deploy,
    )
    run_ship_engine(
        target="spaceship_ftp",
        project_path=str(project),
        run_build=True,
        ship_env={
            "SPACESHIP_FTP_HOST": "ftp.example.test",
            "SPACESHIP_FTP_USER": "user",
            "SPACESHIP_FTP_PASS": "ftp-pass-not-a-real-secret",
            "APP_FEATURE_FLAG": "on",
        },
    )
    assert seen["host"] == "ftp.example.test"
    assert seen["env"] == {"APP_FEATURE_FLAG": "on"}
