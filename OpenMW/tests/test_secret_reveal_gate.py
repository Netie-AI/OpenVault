"""Controls on the plaintext-secret route.

There is no authentication anywhere in this app, so ``/api/keys/{id}/secret``
is the one route that can leak every credential the vault holds. These tests
pin the three cheap controls that stand in for real auth — loopback, explicit
intent, and an audit trail — so none of them can be removed by accident.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from openmw.openvault.app import create_app

REVEAL_HEADER = {"X-OpenVault-Reveal": "intentional"}


def _client(home, host: str = "127.0.0.1") -> TestClient:
    return TestClient(create_app(mock_health=True), client=(host, 5555))


def _make_key(client: TestClient) -> str:
    created = client.post(
        "/api/keys",
        json={
            "label": "reveal-gate",
            "provider": "ollama",
            "secret": "local-no-auth",
            "role": "free",
            "base_url": "http://127.0.0.1:11434",
            "priority": 50,
        },
    )
    assert created.status_code == 200
    return created.json()["id"]


def test_reveal_requires_intent_header(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client(tmp_path)
    key_id = _make_key(client)

    # A plain GET is what a drive-by request from an already-open page would
    # look like; it must not return a secret.
    assert client.get(f"/api/keys/{key_id}/secret").status_code == 428
    # A header with the wrong value is not a near-miss we should be lenient about.
    wrong = client.get(f"/api/keys/{key_id}/secret", headers={"X-OpenVault-Reveal": "yes"})
    assert wrong.status_code == 428


def test_reveal_succeeds_from_loopback_with_intent(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client(tmp_path)
    key_id = _make_key(client)

    ok = client.get(f"/api/keys/{key_id}/secret", headers=REVEAL_HEADER)
    assert ok.status_code == 200
    assert ok.json()["secret"] == "local-no-auth"


def test_reveal_rejected_off_loopback(tmp_path, monkeypatch):
    """A vault reachable from the LAN is not a vault."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    loopback = _client(tmp_path)
    key_id = _make_key(loopback)

    lan = _client(tmp_path, host="192.168.1.50")
    assert lan.get(f"/api/keys/{key_id}/secret", headers=REVEAL_HEADER).status_code == 403


def test_reveal_is_audited(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client(tmp_path)
    key_id = _make_key(client)

    client.get(f"/api/keys/{key_id}/secret", headers=REVEAL_HEADER)

    audit = tmp_path / "secret_audit.jsonl"
    assert audit.exists(), "a secret reveal must leave an audit record"
    entry = json.loads(audit.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["event"] == "secret_reveal"
    assert entry["key_id"] == key_id
    assert entry["client"] == "127.0.0.1"
    # The audit must never become a second place the secret leaks.
    assert "local-no-auth" not in audit.read_text(encoding="utf-8")
