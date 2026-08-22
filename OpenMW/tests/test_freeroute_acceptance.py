"""FreeRoute PRD acceptance: clear refusals (budget / empty / sealed).

Pins Netie-AI/OpenVault#24 — vaulted path needs no client provider key;
exhaustion and sealed vault must return typed errors, never HTTP 500 from
VaultSealedError.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault

_CHAT = {
    "model": "auto",
    "messages": [{"role": "user", "content": "hi"}],
}


@pytest.fixture()
def vault(tmp_path: Path) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


def _client(vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def test_freeroute_prd_refusals_no_client_provider_key(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One seat: empty pool 503, budget 429, sealed-with-metadata not 500."""
    client = _client(vault, tmp_path, monkeypatch)
    # No per-provider key from the caller — vaulted path only. The caller does
    # hold an OpenVault-issued key, which is what selects the metered tier.
    _seat, headers = issue_key(client, label="prd-seat")

    empty = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
    assert empty.status_code == 503
    assert empty.json()["error"]["type"] == "openvault_no_keys"

    saw_429 = False
    for _ in range(25):
        resp = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        if resp.status_code == 429:
            saw_429 = True
            assert resp.json()["error"]["type"] == "rate_limited"
            break
        assert resp.status_code in (502, 503)
    assert saw_429

    # Fresh key = fresh bucket, so budget does not mask the sealed path.
    _sealed_id, sealed_headers = issue_key(client, label="prd-sealed")
    rec = vault.create(
        label="sealed-hop",
        provider="openai",
        secret="sk-sealed-acceptance-aaaaaaaa",
        role="primary",
        base_url="https://example.invalid/v1",
        priority=1,
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)
    vault.seal.lock()
    assert vault.seal.is_sealed

    sealed = client.post("/v1/chat/completions", json=_CHAT, headers=sealed_headers)
    assert sealed.status_code == 403
    assert sealed.status_code != 500
    err = sealed.json()["error"]
    assert err["type"] == "openvault_vault_sealed"
    assert "sealed" in err["message"].lower()
    assert "sk-sealed" not in sealed.text
    assert err["type"] != "openvault_fallback_exhausted"

    stream = client.post(
        "/v1/chat/completions",
        json={**_CHAT, "stream": True},
        headers=sealed_headers,
    )
    assert stream.status_code == 403
    assert stream.json()["error"]["type"] == "openvault_vault_sealed"
    assert "sk-sealed" not in stream.text
