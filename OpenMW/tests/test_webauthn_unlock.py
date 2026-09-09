"""Passkey vault unseal: PRF wrap + ES256 assertion. No live Windows Hello."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault import keywrap, webauthn_unlock
from openmw.openvault.vault.crypto import Seal

PASSPHRASE = "correct-horse-battery-staple"
REVEAL_HEADER = {"X-OpenVault-Reveal": "intentional"}
PRF = webauthn_unlock.b64url(b"prf-secret-32-bytes-pad-pad-pad!!")
CRED_ID = webauthn_unlock.b64url(b"ov-test-cred")


def _client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(create_app(mock_health=True, enable_precheck_loop=False), client=(host, 5555))


def _client_data_b64(*, typ: str, challenge: str, origin: str = "http://127.0.0.1:3010") -> str:
    raw = json.dumps(
        {"type": typ, "challenge": challenge, "origin": origin, "crossOrigin": False},
        separators=(",", ":"),
    ).encode("utf-8")
    return webauthn_unlock.b64url(raw)


def _es256_pair() -> tuple[ec.EllipticCurvePrivateKey, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    spki = private.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, webauthn_unlock.b64url(spki)


def _auth_data(*, uv: bool = True) -> bytes:
    rp_hash = hashlib.sha256(b"127.0.0.1").digest()
    flags = 0x05 if uv else 0x01
    return rp_hash + bytes([flags]) + b"\x00\x00\x00\x01"


def _sign(private: ec.EllipticCurvePrivateKey, auth_data: bytes, client_data: bytes) -> str:
    signed = auth_data + hashlib.sha256(client_data).digest()
    sig = private.sign(signed, ec.ECDSA(hashes.SHA256()))
    return webauthn_unlock.b64url(sig)


def _register_http(client: TestClient) -> ec.EllipticCurvePrivateKey:
    private, spki = _es256_pair()
    begin = client.post("/api/vault/webauthn/register/begin", json={"hybrid": False})
    assert begin.status_code == 200, begin.text
    challenge = begin.json()["publicKey"]["challenge"]
    finish = client.post(
        "/api/vault/webauthn/register/finish",
        json={
            "session_id": begin.json()["session_id"],
            "credential_id": CRED_ID,
            "public_key_spki": spki,
            "client_data_b64": _client_data_b64(typ="webauthn.create", challenge=challenge),
            "extensions": {"prf": {"results": {"first": PRF}}},
        },
    )
    assert finish.status_code == 200, finish.text
    assert finish.json()["webauthn_registered"] is True
    return private


def _unseal_http(client: TestClient, private: ec.EllipticCurvePrivateKey) -> TestClient:
    begin = client.post("/api/vault/webauthn/unseal/begin")
    assert begin.status_code == 200, begin.text
    challenge = begin.json()["publicKey"]["challenge"]
    auth_data = _auth_data()
    client_data_b64 = _client_data_b64(typ="webauthn.get", challenge=challenge)
    client_data = webauthn_unlock.b64url_decode(client_data_b64)
    finish = client.post(
        "/api/vault/webauthn/unseal/finish",
        json={
            "session_id": begin.json()["session_id"],
            "credential_id": CRED_ID,
            "client_data_b64": client_data_b64,
            "authenticator_data_b64": webauthn_unlock.b64url(auth_data),
            "signature_b64": _sign(private, auth_data, client_data),
            "extensions": {"prf": {"results": {"first": PRF}}},
        },
    )
    assert finish.status_code == 200, finish.text
    assert finish.json()["sealed"] is False
    return client


def test_prf_wrap_roundtrip() -> None:
    master = b"0" * 44
    prf = b"x" * 32
    blob = webauthn_unlock.wrap_master(master, prf)
    assert webauthn_unlock.unwrap_master(blob, prf) == master
    with pytest.raises(webauthn_unlock.WebAuthnUnlockError):
        webauthn_unlock.unwrap_master(blob, b"y" * 32)


def test_hybrid_begin_omits_platform_attachment() -> None:
    hybrid = webauthn_unlock.begin_register(hybrid=True)
    assert "authenticatorAttachment" not in hybrid["publicKey"]["authenticatorSelection"]
    assert hybrid["publicKey"]["hints"] == ["hybrid", "client-device"]
    platform = webauthn_unlock.begin_register(hybrid=False)
    assert platform["publicKey"]["authenticatorSelection"]["authenticatorAttachment"] == "platform"


def test_missing_prf_refuses_register(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    begin = client.post("/api/vault/webauthn/register/begin", json={}).json()
    private, spki = _es256_pair()
    del private
    finish = client.post(
        "/api/vault/webauthn/register/finish",
        json={
            "session_id": begin["session_id"],
            "credential_id": CRED_ID,
            "public_key_spki": spki,
            "client_data_b64": _client_data_b64(
                typ="webauthn.create", challenge=begin["publicKey"]["challenge"]
            ),
            "extensions": {},
        },
    )
    assert finish.status_code == 400
    assert "PRF" in finish.json()["detail"]
    assert client.get("/api/vault/status").json()["webauthn_registered"] is False


def test_passkey_unseals_after_passphrase_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    first = _client()
    created = first.post(
        "/api/keys",
        json={
            "label": "before-passkey",
            "provider": "ollama",
            "secret": "sk-live-secret",
            "role": "free",
            "base_url": "http://127.0.0.1:11434",
            "priority": 50,
        },
    )
    assert created.status_code == 200, created.text
    key_id = created.json()["id"]
    private = _register_http(first)
    set_pp = first.post("/api/vault/passphrase", json={"passphrase": PASSPHRASE})
    assert set_pp.status_code == 200
    assert set_pp.json()["wrap_method"] == keywrap.METHOD_PASSPHRASE
    assert set_pp.json()["webauthn_registered"] is True

    restarted = _client()
    status = restarted.get("/api/vault/status").json()
    assert status["sealed"] is True
    assert status["webauthn_registered"] is True
    sealed_reveal = restarted.get(f"/api/keys/{key_id}/secret", headers=REVEAL_HEADER)
    assert sealed_reveal.status_code == 403

    _unseal_http(restarted, private)
    reveal = restarted.get(f"/api/keys/{key_id}/secret", headers=REVEAL_HEADER)
    assert reveal.status_code == 200
    assert reveal.json()["secret"] == "sk-live-secret"


def test_bad_signature_does_not_unseal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    first = _client()
    _register_http(first)
    first.post("/api/vault/passphrase", json={"passphrase": PASSPHRASE})
    client = _client()
    begin = client.post("/api/vault/webauthn/unseal/begin").json()
    other, _spki = _es256_pair()
    auth_data = _auth_data()
    client_data_b64 = _client_data_b64(
        typ="webauthn.get", challenge=begin["publicKey"]["challenge"]
    )
    client_data = webauthn_unlock.b64url_decode(client_data_b64)
    finish = client.post(
        "/api/vault/webauthn/unseal/finish",
        json={
            "session_id": begin["session_id"],
            "credential_id": CRED_ID,
            "client_data_b64": client_data_b64,
            "authenticator_data_b64": webauthn_unlock.b64url(auth_data),
            "signature_b64": _sign(other, auth_data, client_data),
            "extensions": {"prf": {"results": {"first": PRF}}},
        },
    )
    assert finish.status_code == 400
    assert client.get("/api/vault/status").json()["sealed"] is True


def test_activate_from_master_key_keeps_passphrase_wrap(tmp_path: Path) -> None:
    seal = Seal(key_path=tmp_path / "master.key")
    live = seal.copy_master_key()
    seal.set_passphrase(PASSPHRASE)
    seal.lock()
    assert seal.is_sealed
    seal.activate_from_master_key(live)
    assert seal.is_sealed is False
    assert seal.passphrase_configured is True


def test_webauthn_is_loopback_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    remote = _client("192.168.1.50")
    res = remote.post("/api/vault/webauthn/register/begin", json={})
    assert res.status_code == 403
    unseal = remote.post("/api/vault/webauthn/unseal/begin")
    assert unseal.status_code == 403
