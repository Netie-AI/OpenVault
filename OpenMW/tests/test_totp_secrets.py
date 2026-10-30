"""TOTP seed store + gated current-code. RFC 6238 stdlib HMAC. No pyotp."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.secrets import SecretStore, totp_code
from openmw.openvault.vault.store import KeyVault

REVEAL = {"X-OpenVault-Reveal": "intentional"}
# RFC 6238 Appendix B secret (ASCII 12345678901234567890) as base32.
RFC_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    return root


def _client(home: Path) -> TestClient:
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=home / "keys.db", seal=seal)
    secrets = SecretStore(db_path=vault.db_path, seal=seal)
    return TestClient(
        create_app(vault=vault, secrets=secrets, mock_health=True, enable_precheck_loop=False),
        client=("127.0.0.1", 5555),
    )


def test_rfc6238_sha1_6_digits_at_59() -> None:
    key = b"12345678901234567890"
    assert totp_code(key, digits=8, period=30, algorithm="SHA1", at=59) == "94287082"
    assert totp_code(key, digits=6, period=30, algorithm="SHA1", at=59) == "287082"


def test_store_refuses_reveal_and_returns_current_code(home: Path) -> None:
    client = _client(home)
    created = client.post(
        "/api/secrets/totp",
        json={"label": "GitHub", "seed": RFC_SEED, "url": "https://github.com"},
    )
    assert created.status_code == 200, created.text
    row = created.json()
    assert row["kind"] == "totp"
    assert RFC_SEED not in str(row)
    assert "TOTP" in row["masked"]
    listed = client.get("/api/secrets").json()["secrets"]
    assert listed[0]["kind"] == "totp"
    assert RFC_SEED not in str(listed)
    refused = client.get(f"/api/secrets/{row['id']}/reveal", headers=REVEAL)
    assert refused.status_code == 409
    assert "current-code" in refused.text
    code = client.get(f"/api/secrets/{row['id']}/current-code", headers=REVEAL)
    assert code.status_code == 200, code.text
    body = code.json()
    assert body["kind"] == "totp"
    assert body["code"].isdigit()
    assert len(body["code"]) == 6
    assert RFC_SEED not in str(body)
    assert "secret" not in body
    lan = TestClient(
        create_app(mock_health=True, enable_precheck_loop=False),
        client=("192.168.1.50", 5555),
    )
    denied = lan.post("/api/secrets/totp", json={"label": "x", "seed": RFC_SEED})
    assert denied.status_code == 401


def test_agent_retrieve_totp_not_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    retrieve_path = Path(__file__).resolve().parents[2] / "apps" / "cli" / "secret_retrieve.py"
    spec = importlib.util.spec_from_file_location("secret_retrieve", retrieve_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = TestClient(
        create_app(mock_health=True, enable_precheck_loop=False),
        client=("127.0.0.1", 5555),
    )
    row = client.post("/api/secrets/totp", json={"label": "GitHub", "seed": RFC_SEED}).json()

    def http(method: str, url: str, headers: dict | None, _body: bytes | None):
        from urllib.parse import urlparse

        path = urlparse(url).path
        resp = getattr(client, method.lower())(path, headers=headers or {})
        payload = resp.json() if resp.content else {}
        return int(resp.status_code), payload if isinstance(payload, dict) else {}, resp.text

    got = module.retrieve_secret("http://127.0.0.1:5000", row["id"], kind_hint="totp", http=http)
    assert got["kind"] == "totp"
    assert got["code"].isdigit()
    assert RFC_SEED not in str(got)
    auto = module.retrieve_secret("http://127.0.0.1:5000", "GitHub", http=http)
    assert auto["kind"] == "totp"
    assert auto["code"].isdigit()
