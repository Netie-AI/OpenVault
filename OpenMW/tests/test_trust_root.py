"""Custody rules for the signing root and the intermediates it issues.

This is the first authenticator in this application, and the key material it
mints is what lets DMS sign session manifests that the Cortex executor will
honour. Every test here pins a property that, if it quietly stopped holding,
would turn manifest verification into theatre rather than breaking a build.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.trust import TrustError, TrustStore, verify_chain

INTENT = {"X-OpenVault-Reveal": "intentional"}


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def store(home: Path) -> TrustStore:
    return TrustStore(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))


def _client(host: str = "127.0.0.1") -> TestClient:
    # TestClient's default peer host is the literal "testclient", which
    # _require_loopback correctly rejects.
    return TestClient(
        create_app(mock_health=True, enable_precheck_loop=False, cortex_url="http://127.0.0.1:9"),
        client=(host, 5555),
    )


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ── the root ─────────────────────────────────────────────────────────────────


def test_root_is_created_once_and_reused(store: TrustStore) -> None:
    first = store.ensure_root()
    second = store.ensure_root()
    assert first.kid == second.kid
    assert first.kind == "root"


def test_root_private_half_is_sealed_not_plaintext(store: TrustStore, home: Path) -> None:
    """A copy of keys.db must not hand someone the signing root."""
    store.ensure_root()
    raw = (home / "keys.db").read_bytes()
    import sqlite3

    conn = sqlite3.connect(str(home / "keys.db"))
    blob = conn.execute("SELECT private_blob FROM signing_keys WHERE kind='root'").fetchone()[0]
    conn.close()
    assert blob, "root has no stored private half"
    assert b"-----BEGIN" not in raw
    # Fernet tokens start with a version byte of 0x80, base64-encoded as 'gAAAAA'.
    assert blob.startswith(b"gAAAAA"), "root private half is not sealed"


def test_root_is_not_published_in_the_jwks(store: TrustStore) -> None:
    """Nothing should ever accept a manifest signed directly by the root."""
    store.ensure_root()
    store.issue_intermediate("dms")
    kinds = {k["kid"].split("-")[0] for k in store.jwks()["keys"]}
    assert kinds == {"int"}


# ── intermediates ────────────────────────────────────────────────────────────


def test_issued_intermediate_signs_and_verifies(store: TrustStore) -> None:
    issued = store.issue_intermediate("dms", ttl_s=60)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(_b64u_decode(issued.private_key))
    signature = private.sign(b"a manifest would go here")

    published = {k["kid"]: k for k in store.jwks()["keys"]}[issued.record.kid]
    public = Ed25519PublicKey.from_public_bytes(_b64u_decode(published["x"]))
    public.verify(signature, b"a manifest would go here")  # raises if wrong


def test_private_half_is_never_stored(store: TrustStore, home: Path) -> None:
    """Issued once, then gone. A key re-readable from disk leaks with the disk."""
    issued = store.issue_intermediate("dms")
    import sqlite3

    conn = sqlite3.connect(str(home / "keys.db"))
    blob = conn.execute(
        "SELECT private_blob FROM signing_keys WHERE kid=?", (issued.record.kid,)
    ).fetchone()[0]
    conn.close()
    assert blob is None
    assert issued.private_key.encode() not in (home / "keys.db").read_bytes()


def test_intermediate_carries_its_own_expiry(store: TrustStore) -> None:
    """Expiry travels with the key, so an unreachable vault cannot extend it."""
    issued = store.issue_intermediate("dms", ttl_s=30)
    published = {k["kid"]: k for k in store.jwks()["keys"]}[issued.record.kid]
    assert published["exp"] - published["nbf"] == 30


def test_expired_intermediate_drops_out_of_the_jwks(store: TrustStore) -> None:
    issued = store.issue_intermediate("dms", ttl_s=1)
    later = time.time() + 5
    assert issued.record.kid not in {k["kid"] for k in store.jwks(now=later)["keys"]}


def test_revoked_intermediate_drops_out_of_the_jwks(store: TrustStore) -> None:
    issued = store.issue_intermediate("dms", ttl_s=600)
    assert store.revoke_key(issued.record.kid) is True
    assert issued.record.kid not in {k["kid"] for k in store.jwks()["keys"]}


def test_ttl_is_bounded(store: TrustStore) -> None:
    """A long-lived 'short-lived' key is the failure this whole design avoids."""
    with pytest.raises(TrustError):
        store.issue_intermediate("dms", ttl_s=60 * 60 * 24 * 30)
    with pytest.raises(TrustError):
        store.issue_intermediate("dms", ttl_s=0)


# ── the chain ────────────────────────────────────────────────────────────────


def test_chain_verifies_against_the_pinned_root(store: TrustStore) -> None:
    root = store.ensure_root()
    store.issue_intermediate("dms", ttl_s=120)
    jwk = store.jwks()["keys"][0]
    assert verify_chain(root.public_key, jwk) is True


def test_chain_fails_under_a_different_root(store: TrustStore, home: Path) -> None:
    store.issue_intermediate("dms", ttl_s=120)
    jwk = store.jwks()["keys"][0]
    other = TrustStore(db_path=home / "other.db", seal=Seal(Fernet.generate_key()))
    assert verify_chain(other.ensure_root().public_key, jwk) is False


def test_chain_binds_the_subject_and_the_window(store: TrustStore) -> None:
    """Otherwise the root attests to a key without saying who may use it, or until when."""
    root = store.ensure_root()
    store.issue_intermediate("dms", ttl_s=120)
    jwk = dict(store.jwks()["keys"][0])
    assert verify_chain(root.public_key, {**jwk, "netie_subject": "someone-else"}) is False
    assert verify_chain(root.public_key, {**jwk, "exp": jwk["exp"] + 86400}) is False


# ── service identity ─────────────────────────────────────────────────────────


def test_service_token_verifies_and_a_wrong_one_does_not(store: TrustStore) -> None:
    token = store.register_service("dms")
    assert store.verify_service("dms", token) is True
    assert store.verify_service("dms", token + "x") is False
    assert store.verify_service("unknown", token) is False


def test_only_the_token_hash_is_stored(store: TrustStore, home: Path) -> None:
    token = store.register_service("dms")
    assert token.encode() not in (home / "keys.db").read_bytes()


def test_reregistering_rotates_and_invalidates_the_old_token(store: TrustStore) -> None:
    first = store.register_service("dms")
    second = store.register_service("dms")
    assert first != second
    assert store.verify_service("dms", first) is False
    assert store.verify_service("dms", second) is True


def test_revoked_service_cannot_verify(store: TrustStore) -> None:
    token = store.register_service("dms")
    assert store.revoke_service("dms") is True
    assert store.verify_service("dms", token) is False


# ── the routes ───────────────────────────────────────────────────────────────


def test_jwks_is_served_and_is_public(home: Path) -> None:
    """A verifier must not need a credential to learn a public key."""
    client = _client()
    response = client.get("/keys/jwks")
    assert response.status_code == 200
    assert response.json() == {"keys": []}


def test_root_document_is_served(home: Path) -> None:
    response = _client().get("/keys/root")
    assert response.status_code == 200
    body = response.json()
    assert body["kty"] == "OKP" and body["crv"] == "Ed25519"
    assert body["netie_role"] == "trust-root"


def test_full_issue_flow_over_http(home: Path) -> None:
    client = _client()
    registered = client.post("/keys/services", json={"service_id": "dms"}, headers=INTENT)
    assert registered.status_code == 200
    token = registered.json()["token"]

    issued = client.post(
        "/keys/intermediate",
        json={"service_id": "dms", "subject": "dms-manifest-signer", "ttl_s": 300},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert issued.status_code == 200
    payload = issued.json()
    assert payload["alg"] == "EdDSA" and payload["crv"] == "Ed25519"

    published = client.get("/keys/jwks").json()["keys"]
    assert [k["kid"] for k in published] == [payload["kid"]]
    assert verify_chain(client.get("/keys/root").json()["x"], published[0]) is True


def test_issue_without_a_token_is_refused(home: Path) -> None:
    response = _client().post("/keys/intermediate", json={"service_id": "dms"})
    assert response.status_code == 401


def test_issue_with_a_wrong_token_is_refused(home: Path) -> None:
    client = _client()
    client.post("/keys/services", json={"service_id": "dms"}, headers=INTENT)
    response = client.post(
        "/keys/intermediate",
        json={"service_id": "dms"},
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert response.status_code == 403


def test_unknown_and_wrong_service_are_indistinguishable(home: Path) -> None:
    """Telling them apart turns this route into a service-name oracle."""
    client = _client()
    client.post("/keys/services", json={"service_id": "dms"}, headers=INTENT)
    wrong_token = client.post(
        "/keys/intermediate", json={"service_id": "dms"},
        headers={"Authorization": "Bearer wrong"},
    )
    unknown_service = client.post(
        "/keys/intermediate", json={"service_id": "ghost"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert wrong_token.status_code == unknown_service.status_code == 403
    assert wrong_token.json() == unknown_service.json()


def test_registration_requires_the_intent_header(home: Path) -> None:
    response = _client().post("/keys/services", json={"service_id": "dms"})
    assert response.status_code == 428


def test_minting_routes_are_loopback_only(home: Path) -> None:
    remote = _client(host="10.0.0.9")
    register = remote.post("/keys/services", json={"service_id": "dms"}, headers=INTENT)
    assert register.status_code == 403
    assert remote.post("/keys/intermediate", json={"service_id": "dms"}).status_code == 403


def test_no_key_material_appears_in_an_error_body(home: Path) -> None:
    client = _client()
    registered = client.post("/keys/services", json={"service_id": "dms"}, headers=INTENT)
    token = registered.json()["token"]
    bad = client.post(
        "/keys/intermediate",
        json={"service_id": "dms", "ttl_s": 99_999_999},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert bad.status_code == 422
    assert token not in bad.text
