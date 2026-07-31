"""Custody controls on passwords and payment cards.

Companion to ``test_secret_reveal_gate.py``, which pins the same three controls
— loopback, explicit intent, audit — on API keys. Cards raise the stakes: a
leaked API key costs money and can be rotated upstream in a minute, while a
leaked PAN is a fraud problem for the cardholder. So these tests also pin the
things that must be *absent*: the PAN in the database file, in the list
response, and in the audit log.

Card numbers below are the standard vendor test PANs (Luhn-valid, not issued).
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from openmw.openvault.app import create_app

REVEAL_HEADER = {"X-OpenVault-Reveal": "intentional"}

VISA_PAN = "4242424242424242"
VISA_PAN_2 = "4000056655665556"
AMEX_PAN = "378282246310005"


def _client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(create_app(mock_health=True), client=(host, 5555))


def _make_card(client: TestClient, pan: str = VISA_PAN, label: str = "Personal Visa") -> dict:
    res = client.post(
        "/api/secrets/cards",
        json={
            "label": label,
            "pan": pan,
            "exp_month": 11,
            "exp_year": 2029,
            "cardholder": "J HONG",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _make_password(client: TestClient, password: str = "correct-horse-battery") -> dict:
    res = client.post(
        "/api/secrets/passwords",
        json={
            "label": "Netie Space",
            "password": password,
            "username": "oojianhongg@gmail.com",
            "url": "https://netie.space",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


# --- encrypted at rest ---


def test_card_pan_is_never_written_in_the_clear(tmp_path, monkeypatch):
    """The strongest available check: grep the database file itself."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _make_card(client)

    raw = (tmp_path / "keys.db").read_bytes()
    assert VISA_PAN.encode() not in raw, "the PAN reached disk unencrypted"
    # last4 and brand are stored in the clear on purpose — they are what a
    # chooser UI needs and cannot be used to charge the card.
    assert b"4242" in raw


def test_password_is_never_written_in_the_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _make_password(client, "correct-horse-battery")

    raw = (tmp_path / "keys.db").read_bytes()
    assert b"correct-horse-battery" not in raw


def test_list_returns_masks_only(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _make_card(client)
    _make_password(client, "correct-horse-battery")

    body = client.get("/api/secrets").text
    assert VISA_PAN not in body
    assert "correct-horse-battery" not in body

    listed = json.loads(body)["secrets"]
    card = next(s for s in listed if s["kind"] == "payment_card")
    assert card["last4"] == "4242"
    assert card["brand"] == "visa"
    assert card["masked"].endswith("4242")
    assert "pan" not in card and "secret" not in card

    pw = next(s for s in listed if s["kind"] == "password")
    # A password mask must not leak a prefix the way an API key mask does.
    assert set(pw["masked"]) == {"•"}


def test_list_can_filter_by_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _make_card(client)
    _make_password(client)

    cards = client.get("/api/secrets", params={"kind": "payment_card"}).json()["secrets"]
    assert [s["kind"] for s in cards] == ["payment_card"]


# --- reveal gate: the same three controls as /api/keys/{id}/secret ---


def test_reveal_denied_without_intentional_header(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    card = _make_card(client)

    bare = client.get(f"/api/secrets/{card['id']}/reveal")
    assert bare.status_code == 428
    assert VISA_PAN not in bare.text

    wrong = client.get(
        f"/api/secrets/{card['id']}/reveal", headers={"X-OpenVault-Reveal": "yes"}
    )
    assert wrong.status_code == 428


def test_reveal_denied_off_loopback(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    card = _make_card(_client())

    lan = _client(host="192.168.1.50")
    denied = lan.get(f"/api/secrets/{card['id']}/reveal", headers=REVEAL_HEADER)
    assert denied.status_code == 403
    assert VISA_PAN not in denied.text


def test_reveal_returns_pan_from_loopback_with_intent(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    card = _make_card(client)

    ok = client.get(f"/api/secrets/{card['id']}/reveal", headers=REVEAL_HEADER)
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["secret"] == VISA_PAN
    # Enough to confirm you got the card you asked for without parsing the PAN.
    assert payload["kind"] == "payment_card"
    assert payload["last4"] == "4242"


def test_password_reveal_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    pw = _make_password(client, "correct-horse-battery")

    ok = client.get(f"/api/secrets/{pw['id']}/reveal", headers=REVEAL_HEADER)
    assert ok.status_code == 200
    assert ok.json()["secret"] == "correct-horse-battery"


def test_reveal_is_audited_without_leaking_the_pan(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    card = _make_card(client)

    client.get(f"/api/secrets/{card['id']}/reveal", headers=REVEAL_HEADER)

    audit = tmp_path / "secret_audit.jsonl"
    text = audit.read_text(encoding="utf-8")
    entry = json.loads(text.strip().splitlines()[-1])
    assert entry["event"] == "secret_reveal"
    assert entry["secret_id"] == card["id"]
    assert entry["kind"] == "payment_card"
    assert entry["last4"] == "4242"
    assert entry["client"] == "127.0.0.1"
    # The audit must never become a second place the secret leaks.
    assert VISA_PAN not in text


def test_reveal_stamps_last_revealed_at(tmp_path, monkeypatch):
    """Visible in the record itself, so a deleted audit file is not a clean slate."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    card = _make_card(client)
    assert card["last_revealed_at"] is None

    client.get(f"/api/secrets/{card['id']}/reveal", headers=REVEAL_HEADER)
    after = client.get("/api/secrets").json()["secrets"][0]
    assert after["last_revealed_at"] is not None


# --- PCI refusals ---


def test_cvv_is_refused_loudly(tmp_path, monkeypatch):
    """Silently dropping it would let a caller build checkout on a missing field."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()

    res = client.post(
        "/api/secrets/cards",
        json={
            "label": "With CVV",
            "pan": VISA_PAN,
            "exp_month": 11,
            "exp_year": 2029,
            "cvv": "737",
        },
    )
    assert res.status_code == 400
    assert "CVV" in res.json()["detail"]
    assert client.get("/api/secrets").json()["secrets"] == []


def test_bad_pan_is_rejected_and_not_echoed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()

    res = client.post(
        "/api/secrets/cards",
        json={"label": "Typo", "pan": "4242424242424241", "exp_month": 11, "exp_year": 2029},
    )
    assert res.status_code == 400
    assert "Luhn" in res.json()["detail"]
    # A validation error must not hand the rejected number back in the response.
    assert "4242424242424241" not in res.text


def test_bad_expiry_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()

    res = client.post(
        "/api/secrets/cards",
        json={"label": "Bad month", "pan": VISA_PAN, "exp_month": 13, "exp_year": 2029},
    )
    assert res.status_code == 400


def test_brand_is_detected_from_the_iin(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()

    amex = _make_card(client, pan=AMEX_PAN, label="Amex")
    assert amex["brand"] == "amex"
    assert amex["last4"] == "0005"
    assert _make_card(client)["brand"] == "visa"


# --- mutations are loopback-gated and audited ---


def test_card_mutations_are_loopback_only(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    card = _make_card(_client())
    lan = _client(host="192.168.1.50")

    assert lan.delete(f"/api/secrets/{card['id']}").status_code == 403
    assert lan.post(f"/api/secrets/{card['id']}/revoke", json={}).status_code == 403
    assert (
        lan.post(f"/api/secrets/{card['id']}/rotate", json={"new_pan": VISA_PAN_2}).status_code
        == 403
    )
    assert lan.patch(f"/api/secrets/{card['id']}", json={"label": "x"}).status_code == 403
    assert (
        lan.post(
            "/api/secrets/cards",
            json={"label": "remote", "pan": VISA_PAN, "exp_month": 1, "exp_year": 2030},
        ).status_code
        == 403
    )
    # And none of it happened.
    assert len(_client().get("/api/secrets").json()["secrets"]) == 1


def test_card_creation_is_audited_by_last4_only(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    _make_card(_client())

    text = (tmp_path / "secret_audit.jsonl").read_text(encoding="utf-8")
    entry = json.loads(text.strip().splitlines()[-1])
    assert entry["event"] == "card_create"
    assert entry["last4"] == "4242"
    assert VISA_PAN not in text


def test_revoke_keeps_the_record_for_audit(tmp_path, monkeypatch):
    """A revoked card is still the answer to 'what was charged in March'."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    card = _make_card(client)

    revoked = client.post(f"/api/secrets/{card['id']}/revoke", json={"reason": "lost wallet"})
    assert revoked.status_code == 200
    assert revoked.json()["lifecycle"] == "revoked"
    assert len(client.get("/api/secrets").json()["secrets"]) == 1


def test_rotate_card_chains_old_to_new(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    card = _make_card(client)

    rotated = client.post(
        f"/api/secrets/{card['id']}/rotate", json={"new_pan": VISA_PAN_2}
    )
    assert rotated.status_code == 200
    replacement = rotated.json()
    assert replacement["id"] != card["id"]
    assert replacement["last4"] == "5556"
    assert replacement["lifecycle"] == "active"
    # Expiry and cardholder carry over when the caller does not restate them.
    assert replacement["exp_year"] == 2029
    assert replacement["cardholder"] == "J HONG"

    old = next(
        s for s in client.get("/api/secrets").json()["secrets"] if s["id"] == card["id"]
    )
    assert old["lifecycle"] == "rotated"
    assert old["replaced_by"] == replacement["id"]

    # Both PANs still decrypt to the right card.
    assert (
        client.get(f"/api/secrets/{replacement['id']}/reveal", headers=REVEAL_HEADER).json()[
            "secret"
        ]
        == VISA_PAN_2
    )
    assert (
        client.get(f"/api/secrets/{card['id']}/reveal", headers=REVEAL_HEADER).json()["secret"]
        == VISA_PAN
    )


def test_rotate_password_requires_a_new_password(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    pw = _make_password(client)

    assert client.post(f"/api/secrets/{pw['id']}/rotate", json={}).status_code == 400
    ok = client.post(f"/api/secrets/{pw['id']}/rotate", json={"new_password": "next-passphrase"})
    assert ok.status_code == 200
    assert ok.json()["username"] == "oojianhongg@gmail.com"


def test_metadata_patch_cannot_replace_the_payload(tmp_path, monkeypatch):
    """Replacing a PAN in place would erase that the old one ever existed."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    card = _make_card(client)

    patched = client.patch(
        f"/api/secrets/{card['id']}", json={"label": "Renamed", "pan": VISA_PAN_2}
    )
    assert patched.status_code == 200
    assert patched.json()["label"] == "Renamed"
    assert patched.json()["last4"] == "4242"
    assert (
        client.get(f"/api/secrets/{card['id']}/reveal", headers=REVEAL_HEADER).json()["secret"]
        == VISA_PAN
    )


def test_unknown_secret_is_404_not_500(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    assert client.get("/api/secrets/nope/reveal", headers=REVEAL_HEADER).status_code == 404
    assert client.delete("/api/secrets/nope").status_code == 404
