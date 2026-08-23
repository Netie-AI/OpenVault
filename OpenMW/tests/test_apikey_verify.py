"""Loopback verify — Cortex asks OpenVault if an ov_ token is still live.

Run with OpenMW's own env: ``uv run pytest tests/test_apikey_verify.py``.
Do not collect this file via Cortex's venv (playwright plugin + mixed PYTHONPATH).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import issue_key
from openmw.openvault.app import create_app
from openmw.openvault.vault.api_keys import ApiKeyStore


def _client(*, host: str = "127.0.0.1") -> TestClient:
    app = create_app(mock_health=True, enable_precheck_loop=False)
    return TestClient(app, client=(host, 5555))


def test_verify_issued_token_loopback() -> None:
    client = _client()
    _key_id, headers = issue_key(client, label="cortex-bind", tier="free")
    token = headers["Authorization"].removeprefix("Bearer ")
    res = client.post("/api/apikeys/verify", json={"token": token})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["valid"] is True
    assert body["tier"] == "free"
    assert "token" not in body
    assert token not in str(body)


def test_verify_unknown_token_is_valid_false() -> None:
    client = _client()
    res = client.post("/api/apikeys/verify", json={"token": "ov_not_a_real_key"})
    assert res.status_code == 200
    assert res.json() == {"ok": True, "valid": False}


def test_verify_refuses_non_loopback() -> None:
    client = _client(host="8.8.8.8")
    res = client.post("/api/apikeys/verify", json={"token": "ov_whatever"})
    assert res.status_code == 403


def test_store_verify_revoked_is_none(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=tmp_path / "keys.db")
    record, token = store.issue(label="revoke-me", tier="free")
    store.revoke(record.key_id, reason="bind-test")
    assert store.verify(token) is None
