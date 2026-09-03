"""The contract Netie Space depends on, exercised the way Netie actually calls it.

Netie is a thin client: OpenVault is the source of truth for keys
(PRODUCT_ROLES ownership lock 1), and Netie's ``%LOCALAPPDATA%\\NetieSpace\\
user.env`` is an offline cache of AI provider keys only. ``OpenVaultKeySync``
in ``D:\\Space\\src\\NetieSpace\\Services\\OpenVaultKeySync.cs`` does
exactly three things, and each is pinned here:

1. ``GET /api/healthz`` — soft-fail to local keys when the vault is down.
2. ``GET /api/keys`` — list, keeping only ``enabled`` + ``lifecycle=active``.
3. ``GET /api/keys/{id}/secret`` with the intent header, per surviving key.

The reason this file exists separately from ``test_secret_reveal_gate.py`` is
rotation. Rotating a key in OpenVault mints a *new row* and disables the old
one; Netie's filter is what makes the old secret stop being synced. If that
filter or the lifecycle values ever drift apart, Netie silently keeps writing a
dead key into ``user.env`` and every AI call fails with an auth error that
looks like a Netie bug. Also pinned: cards and passwords never appear in the
key list Netie iterates, so there is no path by which a PAN reaches
``user.env``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from openmw.openvault.app import create_app

REVEAL_HEADER = {"X-OpenVault-Reveal": "intentional"}


def _client() -> TestClient:
    return TestClient(create_app(mock_health=True), client=("127.0.0.1", 5555))


def _netie_sync(client: TestClient) -> dict[str, str]:
    """Reproduce OpenVaultKeySync.SyncApiKeysAsync's filter and reveal loop."""
    assert client.get("/api/healthz").status_code == 200
    synced: dict[str, str] = {}
    for key in client.get("/api/keys").json()["keys"]:
        if not key["enabled"] or key["lifecycle"] != "active":
            continue
        revealed = client.get(f"/api/keys/{key['id']}/secret", headers=REVEAL_HEADER)
        assert revealed.status_code == 200
        synced[key["provider"]] = revealed.json()["secret"]
    return synced


def _make_key(client: TestClient, secret: str = "gsk-original") -> dict:
    res = client.post(
        "/api/keys",
        json={
            "label": "GROQ_API_KEY",
            "provider": "groq",
            "secret": secret,
            "role": "primary",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_sync_pulls_the_active_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _make_key(client)

    assert _netie_sync(client) == {"groq": "gsk-original"}


def test_rotate_then_resync_yields_only_the_new_secret(tmp_path, monkeypatch):
    """The acceptance case: rotate in OpenVault, Netie re-syncs and re-masks."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    original = _make_key(client)

    rotated = client.post(f"/api/keys/{original['id']}/rotate", json={"new_secret": "gsk-rotated"})
    assert rotated.status_code == 200
    replacement = rotated.json()

    # The old row survives for audit but is disabled and chained forward, so
    # Netie's enabled+active filter drops it and picks up exactly one secret.
    assert _netie_sync(client) == {"groq": "gsk-rotated"}

    listed = {k["id"]: k for k in client.get("/api/keys").json()["keys"]}
    assert listed[original["id"]]["lifecycle"] == "rotated"
    assert listed[original["id"]]["replaced_by"] == replacement["id"]
    assert listed[replacement["id"]]["lifecycle"] == "active"


def test_key_list_masks_never_carry_the_full_secret(tmp_path, monkeypatch):
    """Netie's Setup UI shows a mask; the list it renders from must be safe."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _make_key(client, "gsk-supersecret-value")

    body = client.get("/api/keys").text
    assert "gsk-supersecret-value" not in body
    assert "masked_secret" in body


def test_revoked_key_stops_syncing(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    key = _make_key(client)

    client.post(f"/api/keys/{key['id']}/revoke", json={"reason": "leaked"})
    assert _netie_sync(client) == {}


def test_cards_and_passwords_are_invisible_to_the_key_sync(tmp_path, monkeypatch):
    """Nothing Netie iterates can hand it a PAN, so user.env cannot acquire one."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _make_key(client)
    client.post(
        "/api/secrets/cards",
        json={"label": "Visa", "pan": "4242424242424242", "exp_month": 11, "exp_year": 2029},
    )
    client.post("/api/secrets/passwords", json={"label": "Netie", "password": "correct-horse"})

    keys_body = client.get("/api/keys").text
    assert "4242424242424242" not in keys_body
    assert "correct-horse" not in keys_body
    assert _netie_sync(client) == {"groq": "gsk-original"}


def test_sync_without_the_intent_header_gets_nothing(tmp_path, monkeypatch):
    """If Netie ever drops the header, it fails closed rather than half-syncing."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    key = _make_key(client)

    assert client.get(f"/api/keys/{key['id']}/secret").status_code == 428
