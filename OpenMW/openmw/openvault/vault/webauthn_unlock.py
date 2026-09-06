"""Platform passkey (Windows Hello / Touch ID / optional iPhone) vault unseal.

Not the Rust console localStorage demo key. Not autofill. Passphrase on disk
stays the backup wrap. This file holds a second wrap of the live master key
under the authenticator PRF secret.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Any

import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from openmw.openvault.paths import ensure_home

log = structlog.get_logger()

RP_ID = "127.0.0.1"
RP_NAME = "OpenVault"
ALLOWED_ORIGINS = frozenset({"http://127.0.0.1:3010", "http://127.0.0.1:5000"})
CHALLENGE_TTL_S = 180
PRF_SALT_LEN = 32

_PENDING: dict[str, dict[str, Any]] = {}


class WebAuthnUnlockError(ValueError):
    """Bad ceremony or missing PRF."""


def _store_path():
    return ensure_home() / "webauthn_unlock.json"


def registered() -> bool:
    path = _store_path()
    if not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return bool(raw.get("credential_id") and raw.get("wrapped_master"))


def status() -> dict[str, Any]:
    path = _store_path()
    hybrid = False
    if path.is_file():
        try:
            hybrid = bool(json.loads(path.read_text(encoding="utf-8")).get("hybrid"))
        except json.JSONDecodeError:
            hybrid = False
    return {
        "webauthn_registered": registered(),
        "webauthn_hybrid": hybrid,
        "rp_id": RP_ID,
    }


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    pad = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii"))


def fernet_key_from_prf(prf: bytes) -> bytes:
    if len(prf) < 16:
        raise WebAuthnUnlockError("PRF secret too short")
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"openvault.webauthn.v1",
        info=b"master-wrap",
    ).derive(prf)
    return base64.urlsafe_b64encode(derived)


def wrap_master(master: bytes, prf: bytes) -> bytes:
    return Fernet(fernet_key_from_prf(prf)).encrypt(master)


def unwrap_master(blob: bytes, prf: bytes) -> bytes:
    try:
        return Fernet(fernet_key_from_prf(prf)).decrypt(blob)
    except InvalidToken as exc:
        raise WebAuthnUnlockError("passkey did not unlock the vault") from exc


def _purge_pending() -> None:
    now = time.time()
    dead = [k for k, v in _PENDING.items() if float(v.get("exp") or 0) < now]
    for key in dead:
        _PENDING.pop(key, None)


def _client_data(raw_b64: str) -> dict[str, Any]:
    parsed = json.loads(b64url_decode(raw_b64).decode("utf-8"))
    if not isinstance(parsed, dict):
        raise WebAuthnUnlockError("clientDataJSON is not an object")
    origin = str(parsed.get("origin") or "")
    if origin not in ALLOWED_ORIGINS:
        raise WebAuthnUnlockError(f"origin {origin!r} is not this laptop console")
    return parsed


def _prf_from_extensions(ext: dict[str, Any]) -> bytes:
    prf = ext.get("prf") if isinstance(ext, dict) else None
    results = prf.get("results") if isinstance(prf, dict) else None
    first = results.get("first") if isinstance(results, dict) else None
    if isinstance(first, str) and first:
        return b64url_decode(first)
    raise WebAuthnUnlockError(
        "this authenticator did not return a PRF secret. "
        "Use Windows Hello / Touch ID, or passphrase. iPhone: try hybrid."
    )


def begin_register(*, hybrid: bool = False) -> dict[str, Any]:
    _purge_pending()
    challenge = secrets.token_bytes(32)
    salt = secrets.token_bytes(PRF_SALT_LEN)
    session_id = secrets.token_hex(8)
    _PENDING[session_id] = {
        "purpose": "register",
        "challenge": b64url(challenge),
        "salt": b64url(salt),
        "hybrid": hybrid,
        "exp": time.time() + CHALLENGE_TTL_S,
    }
    selection: dict[str, Any] = {
        "residentKey": "preferred",
        "userVerification": "required",
    }
    if not hybrid:
        # Omit attachment for iPhone QR / nearby; null is not the same as absent.
        selection["authenticatorAttachment"] = "platform"
    return {
        "session_id": session_id,
        "publicKey": {
            "rp": {"id": RP_ID, "name": RP_NAME},
            "user": {
                "id": b64url(b"openvault-local"),
                "name": "openvault",
                "displayName": "OpenVault",
            },
            "challenge": b64url(challenge),
            "pubKeyCredParams": [
                {"type": "public-key", "alg": -7},
                {"type": "public-key", "alg": -257},
            ],
            "timeout": 120000,
            "attestation": "none",
            "authenticatorSelection": selection,
            "extensions": {"prf": {"eval": {"first": b64url(salt)}}},
            "hints": ["hybrid", "client-device"] if hybrid else ["client-device"],
        },
    }


def finish_register(
    *,
    session_id: str,
    credential_id: str,
    public_key_spki: str,
    client_data_b64: str,
    extensions: dict[str, Any],
    master_key: bytes,
) -> dict[str, Any]:
    pending = _PENDING.pop(session_id.strip(), None)
    if pending is None or pending.get("purpose") != "register":
        raise WebAuthnUnlockError("registration challenge expired")
    data = _client_data(client_data_b64)
    if data.get("type") != "webauthn.create":
        raise WebAuthnUnlockError("not a registration ceremony")
    if data.get("challenge") != pending["challenge"]:
        raise WebAuthnUnlockError("challenge mismatch")
    prf = _prf_from_extensions(extensions)
    cred_id = (credential_id or "").strip()
    spki = (public_key_spki or "").strip()
    if not cred_id or not spki:
        raise WebAuthnUnlockError("credential id and public key are required")
    wrapped = wrap_master(master_key, prf)
    payload = {
        "credential_id": cred_id,
        "public_key_spki": spki,
        "prf_salt": pending["salt"],
        "wrapped_master": wrapped.decode("ascii"),
        "rp_id": RP_ID,
        "hybrid": bool(pending.get("hybrid")),
        "created_at": time.time(),
    }
    _store_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("webauthn_unlock_registered", hybrid=payload["hybrid"])
    return status()


def begin_unseal() -> dict[str, Any]:
    if not registered():
        raise WebAuthnUnlockError("no passkey registered")
    raw = json.loads(_store_path().read_text(encoding="utf-8"))
    _purge_pending()
    challenge = secrets.token_bytes(32)
    session_id = secrets.token_hex(8)
    _PENDING[session_id] = {
        "purpose": "unseal",
        "challenge": b64url(challenge),
        "exp": time.time() + CHALLENGE_TTL_S,
    }
    return {
        "session_id": session_id,
        "publicKey": {
            "rpId": RP_ID,
            "challenge": b64url(challenge),
            "timeout": 120000,
            "userVerification": "required",
            "allowCredentials": [
                {
                    "type": "public-key",
                    "id": raw["credential_id"],
                    "transports": ["internal", "hybrid"],
                }
            ],
            "extensions": {"prf": {"eval": {"first": raw["prf_salt"]}}},
        },
    }


def _load_public_key(spki_b64: str):
    der = b64url_decode(spki_b64)
    return serialization.load_der_public_key(der)


def _verify_assertion(
    *,
    public_key,
    authenticator_data: bytes,
    client_data: bytes,
    signature: bytes,
) -> None:
    if len(authenticator_data) < 37:
        raise WebAuthnUnlockError("authenticatorData too short")
    rp_hash = hashlib.sha256(RP_ID.encode("ascii")).digest()
    if authenticator_data[:32] != rp_hash:
        raise WebAuthnUnlockError("passkey rpId mismatch")
    if authenticator_data[32] & 0x04 == 0:
        raise WebAuthnUnlockError("user verification required")
    signed = authenticator_data + hashlib.sha256(client_data).digest()
    try:
        if isinstance(public_key, EllipticCurvePublicKey):
            public_key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
            return
        public_key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise WebAuthnUnlockError("passkey assertion invalid") from exc


def finish_unseal(
    *,
    session_id: str,
    credential_id: str,
    client_data_b64: str,
    authenticator_data_b64: str,
    signature_b64: str,
    extensions: dict[str, Any],
) -> bytes:
    pending = _PENDING.pop(session_id.strip(), None)
    if pending is None or pending.get("purpose") != "unseal":
        raise WebAuthnUnlockError("unseal challenge expired")
    if not registered():
        raise WebAuthnUnlockError("no passkey registered")
    raw = json.loads(_store_path().read_text(encoding="utf-8"))
    if (credential_id or "").strip() != raw.get("credential_id"):
        raise WebAuthnUnlockError("unknown passkey")
    data = _client_data(client_data_b64)
    if data.get("type") != "webauthn.get":
        raise WebAuthnUnlockError("not an authentication ceremony")
    if data.get("challenge") != pending["challenge"]:
        raise WebAuthnUnlockError("challenge mismatch")
    client_data = b64url_decode(client_data_b64)
    authenticator_data = b64url_decode(authenticator_data_b64)
    signature = b64url_decode(signature_b64)
    public_key = _load_public_key(str(raw["public_key_spki"]))
    _verify_assertion(
        public_key=public_key,
        authenticator_data=authenticator_data,
        client_data=client_data,
        signature=signature,
    )
    prf = _prf_from_extensions(extensions)
    master = unwrap_master(str(raw["wrapped_master"]).encode("ascii"), prf)
    log.info("webauthn_unlock_unsealed")
    return master


def clear_registration() -> None:
    path = _store_path()
    if path.is_file():
        path.unlink()
    log.info("webauthn_unlock_cleared")
