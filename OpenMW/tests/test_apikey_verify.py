"""Loopback verify - Cortex asks OpenVault if an ov_ token is still live.

Cortex has no production keystore of its own. Every live API key it is handed
is a token this vault minted, so ``POST /api/apikeys/verify`` is the call that
lets Cortex accept one. That makes this file an auth-surface test, not a
plumbing test: the assertions below are about what the endpoint refuses and
what it declines to say, as much as about the happy path.

Run with OpenMW's own env: ``uv run pytest tests/test_apikey_verify.py``.
Do not collect this file via Cortex's venv (playwright plugin + mixed
PYTHONPATH).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import issue_key
from openmw.openvault.app import create_app
from openmw.openvault.vault.api_keys import ApiKeyStore


def _client(*, host: str = "127.0.0.1") -> TestClient:
    """A client whose *socket peer* is ``host``.

    Starlette's in-process TestClient otherwise reports the peer as
    ``"testclient"``, which no loopback gate in this repo accepts. Passing
    ``client=`` is the convention every custody test here already follows.
    """
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
    assert body["label"] == "cortex-bind"
    assert "token" not in body
    assert token not in str(body)


def test_verify_never_returns_any_part_of_the_token() -> None:
    """The record carries ``display``, the token's leading characters.

    Returning ``record.to_dict()`` would ship that prefix to every caller. The
    endpoint lists its fields instead, and this asserts the list, so widening
    ``ApiKeyRecord`` later cannot quietly widen the response.
    """
    client = _client()
    _key_id, headers = issue_key(client, label="prefix-check", tier="free")
    token = headers["Authorization"].removeprefix("Bearer ")
    body = client.post("/api/apikeys/verify", json={"token": token}).json()
    assert set(body) == {"ok", "valid", "key_id", "tier", "label"}
    assert "display" not in body
    # Not merely absent as a field: no substring of the credential leaks.
    serialised = str(body)
    for size in (8, 12, 16):
        assert token[:size] not in serialised


def test_verify_unknown_token_is_valid_false() -> None:
    client = _client()
    res = client.post("/api/apikeys/verify", json={"token": "ov_not_a_real_key"})
    assert res.status_code == 200
    assert res.json() == {"ok": True, "valid": False}


def test_verify_revoked_token_is_indistinguishable_from_unknown() -> None:
    """A caller learns the credential does not work, never that it once did."""
    client = _client()
    key_id, headers = issue_key(client, label="revoke-then-verify", tier="free")
    token = headers["Authorization"].removeprefix("Bearer ")
    assert client.delete(f"/api/apikeys/{key_id}").status_code == 200
    revoked = client.post("/api/apikeys/verify", json={"token": token}).json()
    unknown = client.post("/api/apikeys/verify", json={"token": "ov_never_minted"}).json()
    assert revoked == unknown == {"ok": True, "valid": False}


def test_verify_refuses_non_loopback() -> None:
    client = _client(host="8.8.8.8")
    res = client.post("/api/apikeys/verify", json={"token": "ov_whatever"})
    assert res.status_code == 403


def test_verify_refuses_remote_even_with_a_valid_token() -> None:
    """403 is decided before the store is consulted.

    A remote caller holding a good token must still be refused: the gate is the
    peer address, not whether the credential happens to check out.
    """
    local = _client()
    _key_id, headers = issue_key(local, label="remote-holder", tier="free")
    token = headers["Authorization"].removeprefix("Bearer ")
    remote = _client(host="192.168.1.50")
    res = remote.post("/api/apikeys/verify", json={"token": token})
    assert res.status_code == 403
    assert "valid" not in res.json()


def test_verify_ignores_forwarded_headers_claiming_localhost() -> None:
    """A-0009: authority must not travel on the request that asks for it.

    X-Forwarded-For and a self-asserted service identity are strings the
    caller writes about itself. The guard reads ``request.client.host``, so a
    remote peer spelling itself localhost is still refused.
    """
    remote = _client(host="203.0.113.7")
    res = remote.post(
        "/api/apikeys/verify",
        json={"token": "ov_whatever"},
        headers={
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
            "Host": "localhost",
            "x-openfree-identity": "cortex",
        },
    )
    assert res.status_code == 403


def test_verify_accepts_ipv4_mapped_loopback() -> None:
    """The spelling a dual-stack socket reports for this machine.

    Proves the endpoint went through the shared ``_require_loopback`` /
    ``_normalise_host`` guard rather than a second, narrower copy: a hand-rolled
    ``host == "127.0.0.1"`` check would refuse this and break Cortex behind
    Node's proxy hop. R-0005 - a control that refuses legitimate work is a
    failure, not a win.
    """
    client = _client(host="::ffff:127.0.0.1")
    res = client.post("/api/apikeys/verify", json={"token": "ov_not_a_real_key"})
    assert res.status_code == 200
    assert res.json() == {"ok": True, "valid": False}


def test_store_verify_revoked_is_none(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=tmp_path / "keys.db")
    record, token = store.issue(label="revoke-me", tier="free")
    store.revoke(record.key_id, reason="bind-test")
    assert store.verify(token) is None
