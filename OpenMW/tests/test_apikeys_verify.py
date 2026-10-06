"""POST /api/apikeys/verify. Admin or an allowlisted service bearer. Refs #135.

The body token is the key being checked. It is not a credential. Loopback alone
is not enough. Other /api/apikeys routes stay admin-only.
"""

from __future__ import annotations

import hmac
import logging
import sqlite3
from typing import Any

import pytest
from conftest import inject_admin_credential
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from openmw.openvault.app import create_app
from openmw.openvault.paths import keys_db_path
from openmw.openvault.vault.admin_token import admin_headers, path_needs_admin
from openmw.openvault.vault.api_keys import ApiKeyStore
from openmw.openvault.vault.http_guard import (
    VERIFY_SERVICES_ENV,
    apikey_verify_post,
    verify_service_allowlist,
)
from openmw.openvault.vault.trust import TrustStore

INTENT = {"X-OpenVault-Reveal": "intentional"}
_REMOTE = "203.0.113.10"
_ALLOWLISTED_PEER = "10.128.0.3"
_NEGATIVE = {"ok": True, "valid": False}


@pytest.fixture(autouse=True)
def _do_not_inject_admin() -> Any:
    token = inject_admin_credential.set(False)
    yield
    inject_admin_credential.reset(token)


@pytest.fixture()
def home(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.delenv("OPENVAULT_ADMIN_TOKEN_PATH", raising=False)
    monkeypatch.delenv("OPENVAULT_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("OPENVAULT_SERVICES_ALLOW", raising=False)
    monkeypatch.delenv(VERIFY_SERVICES_ENV, raising=False)
    return root


@pytest.fixture()
def app(home: Any) -> FastAPI:
    return create_app(
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )


def _client(app: FastAPI, host: str = "127.0.0.1") -> TestClient:
    return TestClient(app, client=(host, 5555))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_absent(secret: str, blob: str) -> None:
    if secret and secret in blob:
        raise AssertionError("raw key appeared in verify output")


def _register(client: TestClient, service_id: str) -> str:
    response = client.post(
        "/keys/services",
        json={"service_id": service_id},
        headers=INTENT,
    )
    assert response.status_code == 200, response.status_code
    token = response.json()["token"]
    assert isinstance(token, str) and token
    return token


def _issue(client: TestClient) -> tuple[str, str, str]:
    response = client.post(
        "/api/apikeys",
        json={"label": "verify-target", "tier": "free"},
        headers=admin_headers(),
    )
    assert response.status_code == 200, response.status_code
    body = response.json()
    return str(body["key"]["key_id"]), str(body["key"]["tier"]), str(body["token"])


def _set_lifecycle(key_id: str, lifecycle: str) -> None:
    connection = sqlite3.connect(str(keys_db_path()))
    try:
        cursor = connection.execute(
            "UPDATE api_keys SET lifecycle=? WHERE key_id=?",
            (lifecycle, key_id),
        )
        connection.commit()
        assert cursor.rowcount == 1
    finally:
        connection.close()


def _verify(client: TestClient, raw: str, headers: dict[str, str] | None = None) -> Any:
    return client.post("/api/apikeys/verify", json={"token": raw}, headers=headers)


def test_verify_classification_stays_admin_except_exact_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(VERIFY_SERVICES_ENV, raising=False)
    assert verify_service_allowlist() == ()
    monkeypatch.setenv(VERIFY_SERVICES_ENV, " cortex , other ,cortex,")
    assert verify_service_allowlist() == ("cortex", "other")
    assert apikey_verify_post("POST", "/api/apikeys/verify")
    assert apikey_verify_post("post", "/api/apikeys/verify")
    assert not apikey_verify_post("GET", "/api/apikeys/verify")
    assert not apikey_verify_post("PUT", "/api/apikeys/verify")
    assert not apikey_verify_post("DELETE", "/api/apikeys/verify")
    assert not apikey_verify_post("POST", "/api/apikeys/verify/")
    assert not apikey_verify_post("POST", "/api/apikeys")
    assert not apikey_verify_post("POST", "/api/apikeys/verifyX")
    assert path_needs_admin("/api/apikeys")
    assert path_needs_admin("/api/apikeys/verify")


def test_admin_valid_key(app: FastAPI) -> None:
    client = _client(app)
    key_id, tier, raw = _issue(client)
    response = _verify(client, raw, admin_headers())
    _assert_absent(raw, response.text)
    assert response.status_code == 200, response.status_code
    assert response.json() == {"ok": True, "valid": True, "key_id": key_id, "tier": tier}


def test_allowlisted_service_bearer_valid_key(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VERIFY_SERVICES_ENV, "other,cortex")
    local = _client(app)
    service = _register(local, "cortex")
    key_id, tier, raw = _issue(local)
    response = _verify(local, raw, _bearer(service))
    _assert_absent(raw, response.text)
    assert response.status_code == 200, response.status_code
    assert response.json() == {"ok": True, "valid": True, "key_id": key_id, "tier": tier}

    peer = _verify(_client(app, _ALLOWLISTED_PEER), raw, _bearer(service))
    _assert_absent(raw, peer.text)
    assert peer.status_code == 200, peer.status_code
    assert peer.json() == {"ok": True, "valid": True, "key_id": key_id, "tier": tier}


def test_uniform_negative_for_bad_keys(app: FastAPI) -> None:
    client = _client(app)
    headers = admin_headers()
    revoked_id, _tier, revoked_raw = _issue(client)
    deleted = client.delete(f"/api/apikeys/{revoked_id}", headers=headers)
    assert deleted.status_code == 200, deleted.status_code
    disabled_id, _disabled_tier, disabled_raw = _issue(client)
    _set_lifecycle(disabled_id, "disabled")

    samples = (
        "ov_not_a_real_key",
        revoked_raw,
        disabled_raw,
        "",
        "nope",
        "ov_",
        "   ",
    )
    for raw in samples:
        response = _verify(client, raw, headers)
        _assert_absent(raw, response.text)
        assert response.status_code == 200, response.status_code
        assert response.json() == _NEGATIVE
    missing = client.post("/api/apikeys/verify", json={}, headers=headers)
    assert missing.status_code == 200, missing.status_code
    assert missing.json() == _NEGATIVE


def test_no_credential_is_401(app: FastAPI) -> None:
    client = _client(app)
    _key_id, _tier, raw = _issue(client)
    response = _verify(client, raw)
    assert response.status_code == 401, response.status_code
    body = response.json()
    assert body["error"]["type"] == "openvault_unauthenticated"
    _assert_absent(raw, response.text)


def test_unlisted_or_invalid_service_bearer_is_401(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VERIFY_SERVICES_ENV, "other")
    client = _client(app)
    cortex = _register(client, "cortex")
    other = _register(client, "other")
    key_id, tier, raw = _issue(client)

    denied = _verify(client, raw, _bearer(cortex))
    assert denied.status_code == 401, denied.status_code
    _assert_absent(raw, denied.text)
    _assert_absent(cortex, denied.text)

    allowed = _verify(client, raw, _bearer(other))
    _assert_absent(raw, allowed.text)
    assert allowed.status_code == 200, allowed.status_code
    assert allowed.json() == {"ok": True, "valid": True, "key_id": key_id, "tier": tier}

    monkeypatch.setenv(VERIFY_SERVICES_ENV, "cortex")
    garbage = _verify(client, raw, _bearer("not-a-service-token"))
    assert garbage.status_code == 401, garbage.status_code
    _assert_absent(raw, garbage.text)

    via_api_key_header = _verify(client, raw, {"X-API-Key": cortex})
    assert via_api_key_header.status_code == 401, via_api_key_header.status_code


def test_revoked_service_bearer_is_401(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VERIFY_SERVICES_ENV, "cortex")
    client = _client(app)
    service = _register(client, "cortex")
    assert TrustStore(seal=app.state.seal).revoke_service("cortex") is True
    _key_id, _tier, raw = _issue(client)
    response = _verify(client, raw, _bearer(service))
    assert response.status_code == 401, response.status_code
    _assert_absent(raw, response.text)
    _assert_absent(service, response.text)


def test_empty_verify_list_refuses_service_bearer_admin_still_works(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(VERIFY_SERVICES_ENV, raising=False)
    client = _client(app)
    service = _register(client, "cortex")
    key_id, tier, raw = _issue(client)
    denied = _verify(client, raw, _bearer(service))
    assert denied.status_code == 401, denied.status_code
    _assert_absent(raw, denied.text)
    allowed = _verify(client, raw, admin_headers())
    _assert_absent(raw, allowed.text)
    assert allowed.status_code == 200, allowed.status_code
    assert allowed.json() == {"ok": True, "valid": True, "key_id": key_id, "tier": tier}


def test_remote_non_allowlisted_peer_is_denied(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VERIFY_SERVICES_ENV, "cortex")
    local = _client(app)
    service = _register(local, "cortex")
    _key_id, _tier, raw = _issue(local)
    remote = _client(app, _REMOTE)
    with_admin = _verify(remote, raw, admin_headers())
    with_bearer = _verify(remote, raw, _bearer(service))
    assert with_admin.status_code == 403, with_admin.status_code
    assert with_bearer.status_code == 403, with_bearer.status_code
    assert with_admin.json()["error"]["type"] == "openvault_forbidden"
    _assert_absent(raw, with_admin.text)
    _assert_absent(raw, with_bearer.text)
    _assert_absent(service, with_bearer.text)


def test_xff_spoof_from_remote_peer_is_denied(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VERIFY_SERVICES_ENV, "cortex")
    local = _client(app)
    service = _register(local, "cortex")
    _key_id, _tier, raw = _issue(local)
    remote = _client(app, _REMOTE)
    headers = {
        **admin_headers(),
        **_bearer(service),
        "X-Forwarded-For": "127.0.0.1",
        "Host": "127.0.0.1",
    }
    response = _verify(remote, raw, headers)
    assert response.status_code == 403, response.status_code
    assert response.json()["error"]["type"] == "openvault_forbidden"
    _assert_absent(raw, response.text)
    _assert_absent(service, response.text)


def test_raw_key_absent_from_body_and_logs(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(VERIFY_SERVICES_ENV, "cortex")
    client = _client(app)
    service = _register(client, "cortex")
    key_id, _tier, raw = _issue(client)
    caplog.clear()
    with caplog.at_level(logging.DEBUG), capture_logs() as logs:
        response = _verify(client, raw, _bearer(service))
    assert response.status_code == 200, response.status_code
    assert response.json()["valid"] is True
    assert response.json()["key_id"] == key_id
    blob = "\n".join([caplog.text, repr(logs), response.text, str(response.headers)])
    _assert_absent(raw, blob)
    _assert_absent(service, blob)


def test_other_apikey_routes_reject_service_bearer_alone(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VERIFY_SERVICES_ENV, "cortex")
    client = _client(app)
    service = _register(client, "cortex")
    headers = _bearer(service)
    assert client.get("/api/apikeys", headers=headers).status_code == 401
    minted = client.post(
        "/api/apikeys",
        json={"label": "nope", "tier": "free"},
        headers=headers,
    )
    assert minted.status_code == 401, minted.status_code
    assert client.delete("/api/apikeys/whatever", headers=headers).status_code == 401
    assert client.get("/api/apikeys/verify", headers=headers).status_code == 401
    near = client.post("/api/apikeys/verify/", json={"token": "x"}, headers=headers)
    assert near.status_code == 401, near.status_code


def test_negative_paths_each_compare_one_digest(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown, empty, malformed, revoked, and disabled do the same compare."""
    store = ApiKeyStore(db_path=tmp_path / "keys.db")
    revoked, revoked_token = store.issue(label="revoked", tier="free")
    disabled, disabled_token = store.issue(label="disabled", tier="free")
    live, live_token = store.issue(label="live", tier="free")
    assert store.revoke(revoked.key_id) is True
    _set_lifecycle_at(tmp_path / "keys.db", disabled.key_id, "disabled")

    seen: list[tuple[str, str]] = []
    real = hmac.compare_digest

    def _spy(left: object, right: object) -> bool:
        seen.append((str(left), str(right)))
        return bool(real(str(left), str(right)))

    monkeypatch.setattr("openmw.openvault.vault.api_keys.hmac.compare_digest", _spy)
    negatives = ("", "nope", "ov_not-real", revoked_token, disabled_token)
    for item in negatives:
        assert store.match_issued(item) is None
        left, right = seen[-1]
        if item and (item in left or item in right):
            raise AssertionError("raw key reached compare_digest")
        if len(left) != 64 or len(right) != 64:
            raise AssertionError("digest compare was not a fixed-width hash")
    assert len(seen) == len(negatives)
    found = store.match_issued(live_token)
    assert found is not None
    assert found.key_id == live.key_id
    left, right = seen[-1]
    if live_token in left or live_token in right:
        raise AssertionError("raw key reached compare_digest")
    assert len(seen) == len(negatives) + 1


def _set_lifecycle_at(db_path: Any, key_id: str, lifecycle: str) -> None:
    connection = sqlite3.connect(str(db_path))
    try:
        cursor = connection.execute(
            "UPDATE api_keys SET lifecycle=? WHERE key_id=?",
            (lifecycle, key_id),
        )
        connection.commit()
        assert cursor.rowcount == 1
    finally:
        connection.close()


def test_route_lookup_does_not_use_auth_verify(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(self: ApiKeyStore, token: str) -> None:
        raise AssertionError("shared auth verify was used")

    monkeypatch.setattr(ApiKeyStore, "verify", _refuse)
    client = _client(app)
    key_id, _tier, raw = _issue(client)
    live = _verify(client, raw, admin_headers())
    _assert_absent(raw, live.text)
    assert live.status_code == 200, live.status_code
    assert live.json()["key_id"] == key_id
    missing = _verify(client, "ov_not-real", admin_headers())
    assert missing.status_code == 200, missing.status_code
    assert missing.json() == _NEGATIVE


def test_verify_rate_cap_is_429(app: FastAPI) -> None:
    client = _client(app)
    _key_id, _tier, raw = _issue(client)
    headers = admin_headers()
    for _ in range(60):
        response = _verify(client, raw, headers)
        assert response.status_code == 200, response.status_code
        _assert_absent(raw, response.text)
    blocked = _verify(client, raw, headers)
    assert blocked.status_code == 429, blocked.status_code
    _assert_absent(raw, blocked.text)
