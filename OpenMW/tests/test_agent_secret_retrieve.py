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
        resp = call(path, headers=headers or {})
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
