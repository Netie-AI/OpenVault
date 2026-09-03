"""Passphrase KDF + vault unseal/lock gate.

Acceptance (OpenVault#19): after an operator sets a passphrase and the process
restarts, reveal and custody mutations refuse until unseal; lock drops the key
again. DPAPI/plain vaults without a passphrase stay auto-unsealed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault import keywrap
from openmw.openvault.vault.crypto import Seal, VaultSealedError

REVEAL_HEADER = {"X-OpenVault-Reveal": "intentional"}
PASSPHRASE = "correct-horse-battery-staple"


def _client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(create_app(mock_health=True, enable_precheck_loop=False), client=(host, 5555))


def _set_passphrase_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, str]:
    """Simulate process restart after passphrase is configured."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    first = _client()
    status = first.get("/api/vault/status")
    assert status.status_code == 200
    assert status.json()["sealed"] is False

    created = first.post(
        "/api/keys",
        json={
            "label": "before-seal",
            "provider": "ollama",
            "secret": "sk-live-secret",
            "role": "free",
            "base_url": "http://127.0.0.1:11434",
            "priority": 50,
        },
    )
    assert created.status_code == 200, created.text
    key_id = created.json()["id"]

    set_pp = first.post("/api/vault/passphrase", json={"passphrase": PASSPHRASE})
    assert set_pp.status_code == 200, set_pp.text
    assert set_pp.json()["passphrase_configured"] is True
    assert set_pp.json()["wrap_method"] == keywrap.METHOD_PASSPHRASE

    # New process = new Seal load; passphrase-wrapped file starts sealed.
    return _client(), key_id


def test_restart_after_passphrase_starts_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, key_id = _set_passphrase_and_restart(tmp_path, monkeypatch)

    status = client.get("/api/vault/status").json()
    assert status["sealed"] is True
    assert status["passphrase_configured"] is True

    reveal = client.get(f"/api/keys/{key_id}/secret", headers=REVEAL_HEADER)
    assert reveal.status_code == 403
    assert "sealed" in reveal.json()["detail"].lower()
    assert "sk-live-secret" not in reveal.text

    mutate = client.post(
        "/api/keys",
        json={
            "label": "while-sealed",
            "provider": "ollama",
            "secret": "should-not-store",
            "role": "free",
        },
    )
    assert mutate.status_code == 403
    assert "sealed" in mutate.json()["detail"].lower()


def test_unseal_then_reveal_and_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, key_id = _set_passphrase_and_restart(tmp_path, monkeypatch)

    wrong = client.post("/api/vault/unseal", json={"passphrase": "wrong"})
    assert wrong.status_code == 401

    ok = client.post("/api/vault/unseal", json={"passphrase": PASSPHRASE})
    assert ok.status_code == 200, ok.text
    assert ok.json()["sealed"] is False

    reveal = client.get(f"/api/keys/{key_id}/secret", headers=REVEAL_HEADER)
    assert reveal.status_code == 200
    assert reveal.json()["secret"] == "sk-live-secret"

    locked = client.post("/api/vault/lock")
    assert locked.status_code == 200
    assert locked.json()["sealed"] is True

    again = client.get(f"/api/keys/{key_id}/secret", headers=REVEAL_HEADER)
    assert again.status_code == 403
    assert "sk-live-secret" not in again.text


def test_secrets_reveal_and_mutations_gated_while_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    first = _client()
    card = first.post(
        "/api/secrets/cards",
        json={
            "label": "Visa",
            "pan": "4242424242424242",
            "exp_month": 11,
            "exp_year": 2029,
            "cardholder": "J HONG",
        },
    )
    assert card.status_code == 200, card.text
    secret_id = card.json()["id"]
    assert first.post("/api/vault/passphrase", json={"passphrase": PASSPHRASE}).status_code == 200

    client = _client()
    assert client.get("/api/vault/status").json()["sealed"] is True

    reveal = client.get(f"/api/secrets/{secret_id}/reveal", headers=REVEAL_HEADER)
    assert reveal.status_code == 403
    assert "4242424242424242" not in reveal.text

    create_pw = client.post(
        "/api/secrets/passwords",
        json={"label": "x", "password": "nope"},
    )
    assert create_pw.status_code == 403


def test_dpapi_or_plain_vault_stays_auto_unsealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No passphrase configured: restart must not block reveal (copy-protection only)."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    first = _client()
    created = first.post(
        "/api/keys",
        json={
            "label": "auto",
            "provider": "ollama",
            "secret": "local-secret",
            "role": "free",
            "base_url": "http://127.0.0.1:11434",
        },
    )
    assert created.status_code == 200
    key_id = created.json()["id"]
    assert first.get("/api/vault/status").json()["passphrase_configured"] is False

    restarted = _client()
    status = restarted.get("/api/vault/status").json()
    assert status["sealed"] is False
    assert status["passphrase_configured"] is False
    reveal = restarted.get(f"/api/keys/{key_id}/secret", headers=REVEAL_HEADER)
    assert reveal.status_code == 200
    assert reveal.json()["secret"] == "local-secret"


def test_seal_unit_lock_drops_key(tmp_path: Path) -> None:
    key_path = tmp_path / "master.key"
    seal = Seal(key_path=key_path)
    assert not seal.is_sealed
    seal.set_passphrase(PASSPHRASE)
    assert seal.passphrase_configured

    sealed = Seal(key_path=key_path)
    assert sealed.is_sealed
    with pytest.raises(VaultSealedError):
        sealed.encrypt("x")

    sealed.unseal(PASSPHRASE)
    token = sealed.encrypt("x")
    sealed.lock()
    assert sealed.is_sealed
    with pytest.raises(VaultSealedError):
        sealed.decrypt(token)
