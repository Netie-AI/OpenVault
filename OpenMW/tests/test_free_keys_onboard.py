"""#60 Get free keys onboard: Groq-first checklist, env ingest, CF 405 warn.

No live provider accounts. Fixtures are obviously fake. Loopback custody only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.env_ingest import ingest_environment, parse_env_text, scan_environment
from openmw.openvault.vault.free_keys_onboard import (
    CF_MODELS_405_WARN,
    CF_WORKERS_AI_BASE_TEMPLATE,
    FREE_KEYS_ONBOARD,
    compose_cloudflare_workers_ai_base,
    is_cloudflare_workers_ai_base,
    looks_like_site_password_key,
    onboard_install_defaults,
    onboard_payload,
)
from openmw.openvault.vault.precheck import probe_key
from openmw.openvault.vault.providers import get_provider
from openmw.openvault.vault.secrets import SecretStore
from openmw.openvault.vault.store import KeyVault

FAKE_GROQ = "gsk_test_not_a_live_key_0001"
FAKE_CF_TOKEN = "cf-test-token-not-real-0001"
FAKE_ACCOUNT = "a" * 32
FAKE_SITE_PW = "site-login-not-an-api-key"


class _Resp:
    def __init__(self, code: int, body: str = "") -> None:
        self.status_code = code
        self.text = body


class _Client:
    def __init__(self, code: int) -> None:
        self.code = code

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _Resp:
        del url, headers
        body = "Method Not Allowed" if self.code == 405 else ""
        return _Resp(self.code, body)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    return root


@pytest.fixture()
def vault(home: Path) -> KeyVault:
    return KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))


def _client(vault: KeyVault, host: str = "127.0.0.1") -> TestClient:
    return TestClient(
        create_app(vault=vault, mock_health=True, enable_precheck_loop=False),
        client=(host, 5555),
    )


def test_onboard_table_is_groq_first_and_skips_github_models() -> None:
    ids = [item.id for item in FREE_KEYS_ONBOARD]
    assert ids[0] == "groq"
    assert FREE_KEYS_ONBOARD[0].required_first is True
    assert ids == [
        "groq",
        "google",
        "openrouter",
        "cerebras",
        "mistral",
        "huggingface",
        "cloudflare",
    ]
    assert "github_models" not in ids
    payload = onboard_payload([])
    assert payload["github_models"] == "retired"
    assert payload["order"][0] == "groq"
    assert all(row["github_models"] is False for row in payload["providers"])


def test_huggingface_base_url_matches_catalog() -> None:
    spec = get_provider("huggingface")
    assert spec is not None
    hf = next(item for item in FREE_KEYS_ONBOARD if item.id == "huggingface")
    assert hf.default_base_url == spec.base_url == "https://huggingface.co"
    assert hf.add_key_provider == "huggingface"
    assert spec.openai_compatible is False
    assert onboard_install_defaults("huggingface") == ("free", "pooled")


def test_locked_checklist_register_and_base_urls() -> None:
    expected = [
        ("groq", "https://console.groq.com/keys", "https://api.groq.com/openai/v1", "groq"),
        (
            "google",
            "https://aistudio.google.com/apikey",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "google",
        ),
        ("openrouter", "https://openrouter.ai/keys", "https://openrouter.ai/api/v1", "openrouter"),
        ("cerebras", "https://cloud.cerebras.ai", "https://api.cerebras.ai/v1", "cerebras"),
        ("mistral", "https://console.mistral.ai/api-keys", "https://api.mistral.ai/v1", "mistral"),
        (
            "huggingface",
            "https://huggingface.co/settings/tokens",
            "https://huggingface.co",
            "huggingface",
        ),
        (
            "cloudflare",
            "https://developers.cloudflare.com/workers-ai/get-started/rest-api/",
            CF_WORKERS_AI_BASE_TEMPLATE,
            "custom",
        ),
    ]
    assert len(FREE_KEYS_ONBOARD) == len(expected)
    for item, (pid, register_url, base_url, provider) in zip(
        FREE_KEYS_ONBOARD, expected, strict=True
    ):
        assert item.id == pid
        assert item.register_url == register_url
        assert item.default_base_url == base_url
        assert item.add_key_provider == provider
        row = item.to_dict()
        assert row["role"] == "free"
        assert row["custody"] == "pooled"
        assert row["github_models"] is False
    payload = onboard_payload([])
    assert payload["custody"] == "pooled"
    assert payload["keyless"] == "parked"
    assert payload["github_models"] == "retired"


def test_cloudflare_compose_account_id_path() -> None:
    cf = next(item for item in FREE_KEYS_ONBOARD if item.id == "cloudflare")
    assert cf.add_key_provider == "custom"
    assert cf.default_base_url == CF_WORKERS_AI_BASE_TEMPLATE
    url = compose_cloudflare_workers_ai_base(FAKE_ACCOUNT)
    assert url == f"https://api.cloudflare.com/client/v4/accounts/{FAKE_ACCOUNT}/ai/v1"
    assert is_cloudflare_workers_ai_base(url)
    with pytest.raises(ValueError):
        compose_cloudflare_workers_ai_base("short")
    with pytest.raises(ValueError):
        compose_cloudflare_workers_ai_base("https://evil.example/x")
    with pytest.raises(ValueError):
        compose_cloudflare_workers_ai_base("cfatk_" + "x" * 40)


def test_onboard_and_register_api(vault: KeyVault) -> None:
    client = _client(vault)
    onboard = client.get("/api/freeroute/onboard")
    assert onboard.status_code == 200
    body = onboard.json()
    assert body["providers"][0]["id"] == "groq"
    assert [p["id"] for p in body["providers"]] == [
        "groq",
        "google",
        "openrouter",
        "cerebras",
        "mistral",
        "huggingface",
        "cloudflare",
    ]
    catalog = client.get("/api/tool/register")
    assert catalog.status_code == 200
    ids = [row["provider"] for row in catalog.json()["providers"]]
    assert ids[0] == "groq"
    assert "github_models" not in ids
    assert "cloudflare" in ids
    groq = client.get("/api/tool/register", params={"provider": "groq"})
    assert groq.status_code == 200
    assert groq.json()["register_url"] == "https://console.groq.com/keys"
    cf = client.get("/api/tool/register", params={"provider": "cloudflare"})
    assert cf.status_code == 200
    assert cf.json()["add_key_provider"] == "custom"
    retired = client.get("/api/tool/register", params={"provider": "github_models"})
    assert retired.status_code == 404


def test_create_key_fills_catalog_base_url_and_refuses_site_password(vault: KeyVault) -> None:
    client = _client(vault)
    groq = client.post(
        "/api/keys",
        json={
            "label": "Groq",
            "provider": "groq",
            "secret": FAKE_GROQ,
            "role": "free",
            "custody": "pooled",
        },
    )
    assert groq.status_code == 200, groq.text
    spec = get_provider("groq")
    assert spec is not None
    assert groq.json()["base_url"] == spec.base_url
    assert groq.json()["precheck_status"] == "unknown"
    assert groq.json()["role"] == "free"
    assert groq.json()["custody"] == "pooled"
    refuse = client.post(
        "/api/keys",
        json={
            "label": "SITE_PASSWORD",
            "provider": "custom",
            "secret": FAKE_SITE_PW,
            "role": "free",
        },
    )
    assert refuse.status_code == 400
    assert "/api/secrets/passwords" in refuse.text
    assert vault.list_keys()[0].provider == "groq"
    assert looks_like_site_password_key(label="SITE_PASSWORD", provider="custom", base_url="")


def test_lan_cannot_create_or_ingest(vault: KeyVault) -> None:
    lan = _client(vault, host="192.168.1.50")
    assert (
        lan.post(
            "/api/keys",
            json={"label": "Groq", "provider": "groq", "secret": FAKE_GROQ, "role": "free"},
        ).status_code
        == 403
    )
    ingest = lan.post(
        "/api/vault/ingest-env",
        json={"dry_run": False, "env_text": "GROQ_API_KEY=x"},
    )
    assert ingest.status_code == 403


def test_parse_and_ingest_env_text_routes_passwords_to_secrets(vault: KeyVault) -> None:
    text = (
        f"GROQ_API_KEY={FAKE_GROQ}\n"
        f"SITE_PASSWORD={FAKE_SITE_PW}\n"
        f"CLOUDFLARE_API_TOKEN={FAKE_CF_TOKEN}\n"
        f"CLOUDFLARE_ACCOUNT_ID={FAKE_ACCOUNT}\n"
        "HF_TOKEN=hf_test_not_a_live_token_01\n"
    )
    parsed = parse_env_text(text)
    assert parsed["GROQ_API_KEY"] == FAKE_GROQ
    scanned = {c.env_key: c for c in scan_environment(parsed)}
    assert scanned["GROQ_API_KEY"].provider == "groq"
    assert scanned["HF_TOKEN"].provider == "huggingface"
    assert scanned["SITE_PASSWORD"].store == "secrets"
    assert FAKE_SITE_PW not in scanned["SITE_PASSWORD"].masked
    secrets = SecretStore(db_path=vault.db_path, seal=vault.seal)
    dry = ingest_environment(vault, env_text=text, secrets=secrets)
    assert dry["dry_run"] is True
    assert vault.list_keys() == []
    assert secrets.list_secrets() == []
    written = ingest_environment(vault, env_text=text, dry_run=False, secrets=secrets)
    assert FAKE_GROQ not in str(written)
    assert FAKE_SITE_PW not in str(written)
    assert written["imported"] >= 2
    assert written["passwords_imported"] == 1
    providers = {k.provider: k for k in vault.list_keys()}
    assert "groq" in providers
    assert providers["groq"].base_url == get_provider("groq").base_url  # type: ignore[union-attr]
    assert providers["huggingface"].provider == "huggingface"
    assert providers["huggingface"].role == "free"
    assert providers["huggingface"].custody == "pooled"
    assert providers["groq"].role == "free"
    assert providers["groq"].custody == "pooled"
    cf = next(k for k in vault.list_keys() if is_cloudflare_workers_ai_base(k.base_url))
    assert cf.provider == "custom"
    assert cf.role == "free"
    assert cf.custody == "pooled"
    assert cf.base_url.endswith("/ai/v1")
    assert any(s.kind == "password" for s in secrets.list_secrets())
    assert not any(k.provider == "custom" and not k.base_url for k in vault.list_keys())


def test_cf_token_without_account_id_does_not_write_empty_base_url(vault: KeyVault) -> None:
    text = f"CLOUDFLARE_API_TOKEN={FAKE_CF_TOKEN}\n"
    report = ingest_environment(vault, env_text=text, dry_run=False)
    assert report["imported"] == 0
    assert any(r["action"] == "needs_account_id" for r in report["results"])
    assert vault.list_keys() == []


def test_ingest_env_endpoint_accepts_env_text(vault: KeyVault) -> None:
    client = _client(vault)
    dry = client.post(
        "/api/vault/ingest-env",
        json={"dry_run": True, "env_text": f"GROQ_API_KEY={FAKE_GROQ}\n"},
    )
    assert dry.status_code == 200
    assert dry.json()["dry_run"] is True
    assert vault.list_keys() == []
    assert FAKE_GROQ not in dry.text
    written = client.post(
        "/api/vault/ingest-env",
        json={"dry_run": False, "env_text": f"GROQ_API_KEY={FAKE_GROQ}\n"},
    )
    assert written.status_code == 200, written.text
    assert written.json()["imported"] == 1
    assert vault.list_keys()[0].provider == "groq"
    assert vault.list_keys()[0].role == "free"
    assert vault.list_keys()[0].custody == "pooled"


def test_cf_models_405_is_warn_not_dead_and_does_not_fail_save(vault: KeyVault) -> None:
    client = _client(vault)
    base = compose_cloudflare_workers_ai_base(FAKE_ACCOUNT)
    saved = client.post(
        "/api/keys",
        json={
            "label": "Cloudflare Workers AI",
            "provider": "custom",
            "secret": FAKE_CF_TOKEN,
            "role": "free",
            "base_url": base,
            "custody": "pooled",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["precheck_status"] == "unknown"
    assert saved.json()["custody"] == "pooled"
    record = vault.get(saved.json()["id"])
    assert record is not None
    warn = asyncio.run(probe_key(record, FAKE_CF_TOKEN, client=_Client(405)))
    assert warn.status == "ok"
    assert warn.error == CF_MODELS_405_WARN
    dead = asyncio.run(probe_key(record, FAKE_CF_TOKEN, client=_Client(401)))
    assert dead.status == "auth_fail"
    groq = vault.create(
        label="Groq",
        provider="groq",
        secret=FAKE_GROQ,
        role="free",
        base_url="https://api.groq.com/openai/v1",
    )
    groq_405 = asyncio.run(probe_key(groq, FAKE_GROQ, client=_Client(405)))
    assert groq_405.status == "error"


def test_wizard_source_is_keys_only_and_opens_register_url() -> None:
    root = Path(__file__).resolve().parents[2]
    wizard = (root / "apps/web/src/app/keys/FreeKeysWizard.tsx").read_text(encoding="utf-8")
    table = (root / "apps/web/src/lib/vault/freeKeysOnboard.ts").read_text(encoding="utf-8")
    assert "createPassword" not in wizard
    assert "site-pw" not in wizard
    assert 'custody: "pooled"' in wizard
    assert 'role: "free"' in wizard
    assert "GitHub Models is retired" in wizard
    assert "openvault app" in wizard
    assert "keyless" in wizard.lower()
    assert "https://console.groq.com/keys" in table
    assert "https://huggingface.co/settings/tokens" in table
