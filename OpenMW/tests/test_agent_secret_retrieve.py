"""Agent thin-client retrieve: keys + passwords, hard-deny PAN (#39).

Uses the existing reveal gates. The client refuses payment_card even if a
naive agent asks. Retrieved passwords are never written under OPENVAULT_HOME.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app

REVEAL_HEADER = {"X-OpenVault-Reveal": "intentional"}
CLI_RETRIEVE = Path(__file__).resolve().parents[2] / "apps" / "cli" / "secret_retrieve.py"
VISA_PAN = "4242424242424242"


def _load_retrieve():
    spec = importlib.util.spec_from_file_location("secret_retrieve", CLI_RETRIEVE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    return TestClient(
        create_app(mock_health=True, enable_precheck_loop=False),
        client=("127.0.0.1", 5555),
    )


def _http(client: TestClient):
    def fn(method: str, url: str, headers: dict[str, str] | None, _body: bytes | None):
        parsed = urlparse(url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        call = getattr(client, method.lower())
        kwargs: dict = {"headers": headers or {}}
        if _body:
            kwargs["content"] = _body
        resp = call(path, **kwargs)
        payload: dict = {}
        try:
            loaded = resp.json()
        except Exception:
            loaded = {}
        if isinstance(loaded, dict):
            payload = loaded
        return int(resp.status_code), payload, resp.text

    return fn


def test_retrieves_api_key_and_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retrieve = _load_retrieve()
    client = _client(tmp_path, monkeypatch)
    key = client.post(
        "/api/keys",
        json={"label": "openai-prod", "provider": "openai", "secret": "sk-agent-aaaaaaa"},
    ).json()
    pw = client.post(
        "/api/secrets/passwords",
        json={
            "label": "Netie Space",
            "password": "correct-horse-battery",
            "username": "builder",
            "url": "https://netie.space",
        },
    ).json()

    http = _http(client)
    got_key = retrieve.retrieve_secret(
        "http://127.0.0.1:5000", key["id"], kind_hint="key", http=http
    )
    got_pw = retrieve.retrieve_secret(
        "http://127.0.0.1:5000", pw["id"], kind_hint="password", http=http
    )
    assert got_key["secret"] == "sk-agent-aaaaaaa"
    assert got_key["cached"] is False
    assert got_pw["secret"] == "correct-horse-battery"
    assert got_pw["kind"] == "password"
    assert got_pw["cached"] is False

    leftover = [
        p
        for p in tmp_path.rglob("*")
        if p.is_file()
        and p.suffix not in {".db", ".jsonl", ".key"}
        and "correct-horse-battery" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert leftover == [], leftover
    assert not any(p.name.endswith(".password") for p in tmp_path.rglob("*"))


def test_list_omits_cards_and_plaintext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retrieve = _load_retrieve()
    client = _client(tmp_path, monkeypatch)
    pw = client.post(
        "/api/secrets/passwords",
        json={"label": "Netie Space", "password": "correct-horse-battery"},
    ).json()
    totp = client.post(
        "/api/secrets/totp",
        json={"label": "GitHub", "seed": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"},
    ).json()
    client.post(
        "/api/secrets/cards",
        json={"label": "Personal Visa", "pan": VISA_PAN, "exp_month": 11, "exp_year": 2029},
    )
    listed = retrieve.list_secrets("http://127.0.0.1:5000", http=_http(client))
    kinds = {row["kind"] for row in listed["secrets"]}
    labels = {row["label"] for row in listed["secrets"]}
    assert listed["count"] == 2
    assert kinds == {"password", "totp"}
    assert "Netie Space" in labels and "GitHub" in labels
    blob = str(listed)
    assert "correct-horse-battery" not in blob
    assert VISA_PAN not in blob
    assert totp["id"] in {row["id"] for row in listed["secrets"]}
    assert pw["id"] in {row["id"] for row in listed["secrets"]}
    with pytest.raises(retrieve.RetrieveError, match="PAN"):
        retrieve.list_secrets("http://127.0.0.1:5000", kind_hint="payment_card", http=_http(client))


def test_hard_denies_payment_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retrieve = _load_retrieve()
    client = _client(tmp_path, monkeypatch)
    card = client.post(
        "/api/secrets/cards",
        json={
            "label": "Personal Visa",
            "pan": VISA_PAN,
            "exp_month": 11,
            "exp_year": 2029,
        },
    ).json()
    http = _http(client)
    with pytest.raises(retrieve.RetrieveError, match="PAN") as excinfo:
        retrieve.retrieve_secret("http://127.0.0.1:5000", card["id"], http=http)
    assert VISA_PAN not in str(excinfo.value)

    with pytest.raises(retrieve.RetrieveError, match="PAN"):
        retrieve.retrieve_secret(
            "http://127.0.0.1:5000", card["id"], kind_hint="payment_card", http=http
        )

    human = client.get(f"/api/secrets/{card['id']}/reveal", headers=REVEAL_HEADER)
    assert human.status_code == 200
    assert human.json()["secret"] == VISA_PAN


def test_sealed_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retrieve = _load_retrieve()
    client = _client(tmp_path, monkeypatch)
    key = client.post(
        "/api/keys",
        json={"label": "sealed-key", "provider": "openai", "secret": "sk-sealed-aaaaaaa"},
    ).json()
    client.post("/api/vault/passphrase", json={"passphrase": "a-long-enough-phrase"})
    client.post("/api/vault/lock")
    http = _http(client)
    with pytest.raises(retrieve.RetrieveError, match="sealed"):
        retrieve.retrieve_secret("http://127.0.0.1:5000", key["id"], kind_hint="key", http=http)


def test_unique_substring_get_and_list_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retrieve = _load_retrieve()
    client = _client(tmp_path, monkeypatch)
    client.post(
        "/api/secrets/passwords",
        json={
            "label": "SITE_accounts_google_com",
            "password": "site-login-not-an-api-key",
            "url": "https://accounts.google.com",
        },
    )
    client.post(
        "/api/secrets/passwords",
        json={
            "label": "SITE_github_com",
            "password": "other-site-login",
            "url": "https://github.com",
        },
    )
    client.post(
        "/api/secrets/totp",
        json={"label": "GitHub OTP", "seed": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"},
    )
    http = _http(client)
    listed = retrieve.list_secrets(
        "http://127.0.0.1:5000", kind_hint="password", query="google", http=http
    )
    assert listed["count"] == 1
    assert listed["secrets"][0]["label"] == "SITE_accounts_google_com"
    assert "site-login-not-an-api-key" not in str(listed)
    got = retrieve.retrieve_secret(
        "http://127.0.0.1:5000", "accounts.google.com", kind_hint="password", http=http
    )
    assert got["kind"] == "password"
    assert got["secret"] == "site-login-not-an-api-key"
    totp = retrieve.retrieve_secret(
        "http://127.0.0.1:5000", "GitHub OTP", kind_hint="totp", http=http
    )
    assert totp["kind"] == "totp"
    assert str(totp["code"]).isdigit()
    with pytest.raises(retrieve.RetrieveError, match="ambiguous"):
        retrieve.retrieve_secret("http://127.0.0.1:5000", "SITE_", kind_hint="password", http=http)


def test_site_target_does_not_fall_through_to_leftover_hop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    from openmw.openvault.vault.crypto import Seal
    from openmw.openvault.vault.store import KeyVault

    retrieve = _load_retrieve()
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=Seal(Fernet.generate_key()))
    leftover = vault.create(
        label="SITE_PASSWORD",
        provider="custom",
        secret="leftover-site-as-hop",
        role="free",
        base_url="",
    )
    client = TestClient(
        create_app(vault=vault, mock_health=True, enable_precheck_loop=False),
        client=("127.0.0.1", 5555),
    )
    http = _http(client)
    listed = {row["id"] for row in client.get("/api/keys").json()["keys"]}
    assert leftover.id not in listed
    got = retrieve.retrieve_secret("http://127.0.0.1:5000", "SITE_PASSWORD", http=http)
    assert got["kind"] == "password"
    assert got["secret"] == "leftover-site-as-hop"
    assert leftover.id not in {row["id"] for row in client.get("/api/keys").json()["keys"]}
    with pytest.raises(retrieve.RetrieveError, match="site passwords are /api/secrets"):
        retrieve.retrieve_secret(
            "http://127.0.0.1:5000", "SITE_PASSWORD", kind_hint="key", http=http
        )
