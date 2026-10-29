"""Host adapters take credentials from the vault-backed ship env."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.engine import run_ship_engine
from openmw.openvault.ship.hosts.base import DeployResult
from openmw.openvault.ship.inject import vault_host_env
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.secrets import SecretStore
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


def test_cloudflare_deploy_uses_ship_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    project = tmp_path / "light"
    project.mkdir()
    (project / "index.html").write_text("<h1>light</h1>", encoding="utf-8")
    token = "cf-token-not-a-real-secret"
    account = "acct-123"
    seen: dict[str, str] = {}

    def fake_deploy(self: object, artifact_dir: Path, *, project: str) -> DeployResult:
        seen["token"] = self._token
        seen["account"] = self._account
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
        seen["host"] = self._host
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


# --- PR #170 review: custody, account, determinism, decrypt scope, audit ---

POOLED_TOKEN = "cf-pooled-not-a-real-secret"
TENANT_TOKEN = "cf-tenant-not-a-real-secret"
ACCOUNT_TOKEN = "cf-account-bound-not-a-real-secret"


def _vault(tmp_path: Path) -> KeyVault:
    return KeyVault(db_path=tmp_path / "keys.db", seal=Seal(Fernet.generate_key()))


def _set_created_at(vault: KeyVault, key_id: str, created_at: float) -> None:
    """Pin ordering so tests never depend on the clock's resolution."""
    conn = sqlite3.connect(str(vault.db_path))
    try:
        with conn:
            conn.execute("UPDATE keys SET created_at = ? WHERE id = ?", (created_at, key_id))
    finally:
        conn.close()


def _spy_get_secret(vault: KeyVault, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every key id the vault decrypts."""
    opened: list[str] = []
    real = vault.get_secret

    def spy(key_id: str) -> str | None:
        opened.append(key_id)
        return real(key_id)

    monkeypatch.setattr(vault, "get_secret", spy)
    return opened


def test_tenant_row_never_beats_pooled_row_of_same_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DR-0009: a custody=tenant token must not replace the operator's pooled one."""
    vault = _vault(tmp_path)
    pooled = vault.create(
        label="CLOUDFLARE_API_TOKEN", provider="custom", secret=POOLED_TOKEN, role="primary"
    )
    tenant = vault.create(
        label="CLOUDFLARE_API_TOKEN",
        provider="custom",
        secret=TENANT_TOKEN,
        role="primary",
        custody="tenant",
    )
    # The tenant row is the newest, which is exactly what used to make it win.
    _set_created_at(vault, pooled.id, 1_000.0)
    _set_created_at(vault, tenant.id, 2_000.0)
    opened = _spy_get_secret(vault, monkeypatch)

    assert vault_host_env(vault)["CLOUDFLARE_API_TOKEN"] == POOLED_TOKEN
    assert opened == [pooled.id], "the tenant row must not even be decrypted"


def test_tenant_only_row_supplies_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    vault.create(
        label="NETLIFY_AUTH_TOKEN",
        provider="custom",
        secret=TENANT_TOKEN,
        role="primary",
        custody="tenant",
    )
    opened = _spy_get_secret(vault, monkeypatch)
    assert vault_host_env(vault) == {}
    assert opened == []


def test_account_bound_row_is_never_chosen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    plain = vault.create(
        label="CLOUDFLARE_API_TOKEN", provider="custom", secret=POOLED_TOKEN, role="primary"
    )
    bound = vault.create(
        label="CLOUDFLARE_API_TOKEN",
        provider="custom",
        secret=ACCOUNT_TOKEN,
        role="primary",
        account_id="acct-provider",
    )
    _set_created_at(vault, plain.id, 1_000.0)
    _set_created_at(vault, bound.id, 2_000.0)
    opened = _spy_get_secret(vault, monkeypatch)

    assert vault_host_env(vault)["CLOUDFLARE_API_TOKEN"] == POOLED_TOKEN
    assert opened == [plain.id]

    other = tmp_path / "other"
    other.mkdir()
    only_bound = _vault(other)
    only_bound.create(
        label="COOLIFY_TOKEN",
        provider="custom",
        secret=ACCOUNT_TOKEN,
        role="primary",
        account_id="acct-provider",
    )
    assert vault_host_env(only_bound) == {}


def test_most_recent_active_pooled_row_wins_and_only_it_is_decrypted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _vault(tmp_path)
    older = vault.create(label="COOLIFY_TOKEN", provider="custom", secret="old-tok", role="primary")
    newer = vault.create(label="COOLIFY_TOKEN", provider="custom", secret="new-tok", role="primary")
    revoked = vault.create(
        label="COOLIFY_TOKEN", provider="custom", secret="revoked-tok", role="primary"
    )
    disabled = vault.create(
        label="COOLIFY_TOKEN",
        provider="custom",
        secret="disabled-tok",
        role="primary",
        enabled=False,
    )
    vault.revoke(revoked.id)
    _set_created_at(vault, older.id, 1_000.0)
    _set_created_at(vault, newer.id, 2_000.0)
    _set_created_at(vault, revoked.id, 3_000.0)
    _set_created_at(vault, disabled.id, 4_000.0)
    opened = _spy_get_secret(vault, monkeypatch)

    assert vault_host_env(vault)["COOLIFY_TOKEN"] == "new-tok"
    assert opened == [newer.id]


def test_created_at_tie_resolves_to_the_same_row_every_time(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    rows = [
        vault.create(label="COOLIFY_TOKEN", provider="custom", secret=f"tok-{i}", role="primary")
        for i in range(4)
    ]
    for row in rows:
        _set_created_at(vault, row.id, 5_000.0)
    expected = vault.get_secret(max(rows, key=lambda r: r.id).id)
    assert {vault_host_env(vault)["COOLIFY_TOKEN"] for _ in range(5)} == {expected}


def test_on_decrypt_reports_label_and_key_ref_only(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    cf = vault.create(
        label="CLOUDFLARE_API_TOKEN", provider="custom", secret=POOLED_TOKEN, role="primary"
    )
    vault.create(label="GROQ_API_KEY", provider="groq", secret="gsk-not-a-host", role="primary")
    seen: list[tuple[str, str]] = []
    env = vault_host_env(vault, on_decrypt=lambda label, ref: seen.append((label, ref)))
    assert env == {"CLOUDFLARE_API_TOKEN": POOLED_TOKEN}
    assert seen == [("CLOUDFLARE_API_TOKEN", cf.id[:8])]
    assert POOLED_TOKEN not in repr(seen)


def test_sealed_vault_decrypts_and_reports_nothing(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.create(
        label="CLOUDFLARE_API_TOKEN", provider="custom", secret=POOLED_TOKEN, role="primary"
    )
    vault.seal.lock()
    seen: list[tuple[str, str]] = []
    assert vault_host_env(vault, on_decrypt=lambda label, ref: seen.append((label, ref))) == {}
    assert seen == []


# --- route level: POST /api/ship/engine ---


def _audit_text(home: Path) -> str:
    path = home / "secret_audit.jsonl"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _audit_lines(home: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in _audit_text(home).splitlines() if line.strip()]


class _ShipRoute:
    """Real /api/ship/engine route with the engine stubbed to record its ship_env."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = tmp_path / "ovhome"
        monkeypatch.setenv("OPENVAULT_HOME", str(self.home))
        self.vault = KeyVault(db_path=self.home / "keys.db", seal=Seal(Fernet.generate_key()))
        self.secrets = SecretStore(db_path=self.vault.db_path, seal=self.vault.seal)
        self.engine_calls: list[dict[str, object]] = []
        self.echo = ""
        project = tmp_path / "proj"
        project.mkdir()
        (project / "index.html").write_text("<h1>x</h1>", encoding="utf-8")
        self.project = str(project)

        def fake_engine(**kwargs: object) -> dict[str, object]:
            self.engine_calls.append(kwargs)
            return {"ok": True, "echo": self.echo, "deployment": {"target": kwargs["target"]}}

        monkeypatch.setattr("openmw.openvault.app.run_ship_engine", fake_engine)
        # The leave gate has its own tests; this file is about what the route decrypts.
        monkeypatch.setattr("openmw.openvault.app._require_leave_gate", lambda *a, **k: None)
        self.client = TestClient(
            create_app(
                vault=self.vault,
                secrets=self.secrets,
                mock_health=True,
                enable_precheck_loop=False,
            ),
            client=("127.0.0.1", 5555),
        )

    def ship(self, target: str, secrets: list[dict[str, str]] | None = None) -> dict[str, object]:
        resp = self.client.post(
            "/api/ship/engine",
            json={
                "target": target,
                "project_path": self.project,
                "hostname": "app.example.test",
                "secrets": secrets or [],
            },
        )
        assert resp.status_code == 200, resp.text
        body: dict[str, object] = resp.json()
        return body

    def sent_env(self) -> dict[str, str]:
        assert len(self.engine_calls) == 1
        env = self.engine_calls[0]["ship_env"]
        assert isinstance(env, dict)
        return env

    def host_audit(self) -> list[dict[str, object]]:
        return [e for e in _audit_lines(self.home) if e.get("event") == "ship_host_credential"]


@pytest.mark.parametrize("target", ["local_demo", "aws_guide"])
def test_local_ship_target_decrypts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    route = _ShipRoute(tmp_path, monkeypatch)
    route.vault.create(
        label="CLOUDFLARE_API_TOKEN", provider="custom", secret=POOLED_TOKEN, role="primary"
    )
    route.vault.create(
        label="SPACESHIP_FTP_PASS", provider="custom", secret="ftp-not-real", role="primary"
    )
    opened = _spy_get_secret(route.vault, monkeypatch)

    route.ship(target)

    assert opened == [], "a target that never leaves the machine must not open host rows"
    assert len(route.engine_calls) == 1
    assert route.engine_calls[0]["ship_env"] is None
    assert route.host_audit() == []
    assert POOLED_TOKEN not in _audit_text(route.home)


def test_remote_ship_target_audits_each_host_credential_without_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = _ShipRoute(tmp_path, monkeypatch)
    cf = route.vault.create(
        label="CLOUDFLARE_API_TOKEN", provider="custom", secret=POOLED_TOKEN, role="primary"
    )
    acct = route.vault.create(
        label="CLOUDFLARE_ACCOUNT_ID", provider="custom", secret="acct-id-not-real", role="primary"
    )
    route.vault.create(label="GROQ_API_KEY", provider="groq", secret="gsk-no", role="primary")
    route.echo = f"token was {POOLED_TOKEN}"

    body = route.ship("cloudflare_pages")

    env = route.sent_env()
    assert env["CLOUDFLARE_API_TOKEN"] == POOLED_TOKEN
    assert env["CLOUDFLARE_ACCOUNT_ID"] == "acct-id-not-real"
    assert "GROQ_API_KEY" not in env
    assert POOLED_TOKEN not in json.dumps(body), "response must be scrubbed"

    lines = route.host_audit()
    assert sorted((str(e["label"]), str(e["key_ref"])) for e in lines) == sorted(
        [("CLOUDFLARE_API_TOKEN", cf.id[:8]), ("CLOUDFLARE_ACCOUNT_ID", acct.id[:8])]
    )
    assert all(e["target"] == "cloudflare_pages" for e in lines)
    raw = _audit_text(route.home)
    for value in (POOLED_TOKEN, "acct-id-not-real"):
        assert value not in raw
    for entry in lines:
        assert set(entry) <= {"ts", "event", "target", "label", "key_ref", "client", "user_agent"}


def test_remote_ship_target_uses_pooled_row_over_newer_tenant_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = _ShipRoute(tmp_path, monkeypatch)
    pooled = route.vault.create(
        label="CLOUDFLARE_API_TOKEN", provider="custom", secret=POOLED_TOKEN, role="primary"
    )
    tenant = route.vault.create(
        label="CLOUDFLARE_API_TOKEN",
        provider="custom",
        secret=TENANT_TOKEN,
        role="primary",
        custody="tenant",
    )
    _set_created_at(route.vault, pooled.id, 1_000.0)
    _set_created_at(route.vault, tenant.id, 2_000.0)

    route.ship("cloudflare_pages")

    assert route.sent_env()["CLOUDFLARE_API_TOKEN"] == POOLED_TOKEN
    assert [e["key_ref"] for e in route.host_audit()] == [pooled.id[:8]]
    assert TENANT_TOKEN not in _audit_text(route.home)


def test_request_binding_cannot_override_a_vault_host_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller must not point COOLIFY_URL elsewhere while the vault supplies the token."""
    route = _ShipRoute(tmp_path, monkeypatch)
    route.vault.create(
        label="COOLIFY_URL",
        provider="custom",
        secret="https://coolify.operator.test",
        role="primary",
    )
    route.vault.create(
        label="COOLIFY_TOKEN", provider="custom", secret="coolify-tok-not-real", role="primary"
    )
    evil_url = route.secrets.create_password(label="evil-url", password="https://evil.example")
    fallback_uuid = route.secrets.create_password(label="uuid", password="app-uuid-from-binding")
    app_flag = route.secrets.create_password(label="flag", password="flag-on")
    route.echo = "pointed at https://evil.example"

    body = route.ship(
        "coolify",
        secrets=[
            {"env_name": "COOLIFY_URL", "secret_id": evil_url.id},
            {"env_name": "COOLIFY_APP_UUID", "secret_id": fallback_uuid.id},
            {"env_name": "APP_FEATURE_FLAG", "secret_id": app_flag.id},
        ],
    )

    env = route.sent_env()
    assert env["COOLIFY_URL"] == "https://coolify.operator.test"
    assert env["COOLIFY_TOKEN"] == "coolify-tok-not-real"
    # The vault holds no COOLIFY_APP_UUID, so the binding is still the fallback.
    assert env["COOLIFY_APP_UUID"] == "app-uuid-from-binding"
    assert env["APP_FEATURE_FLAG"] == "flag-on"
    # The overridden binding was a secret the caller selected: still scrubbed.
    assert "evil.example" not in json.dumps(body)
