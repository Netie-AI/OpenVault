"""Signing mint skips X-OpenVault-Admin (#126). Custody stays on the route.

Prove ``POST /keys/services`` sends only ``X-OpenVault-Reveal: intentional``.
The admin gate was answering 401 ``openvault_unauthenticated`` before the #52
peer allowlist ran.

This module sends no admin header. ``path_needs_admin`` stays true for
``/keys`` so GET and near paths still require the admin credential.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from conftest import inject_admin_credential, issue_key
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.admin_token import (
    ADMIN_HEADER,
    ensure_admin_token,
    path_needs_admin,
)
from openmw.openvault.vault.http_guard import signing_mint_kind

INTENT = {"X-OpenVault-Reveal": "intentional"}
_REMOTE = "203.0.113.10"
_SERVICE = {"service_id": "dms"}

_SPOOF_HEADERS: tuple[dict[str, str], ...] = (
    {"X-Forwarded-For": "10.128.0.3", **INTENT},
    {"X-Forwarded-For": "127.0.0.1", **INTENT},
    {"X-Real-IP": "10.128.0.3", **INTENT},
    {"Host": "10.128.0.3", **INTENT},
    {"Host": "127.0.0.1", **INTENT},
    {"Forwarded": "for=10.128.0.3;proto=http", **INTENT},
    {
        "X-Forwarded-For": "10.128.0.3, 127.0.0.1",
        "X-Real-IP": "127.0.0.1",
        "Host": "127.0.0.1",
        "Forwarded": "for=127.0.0.1",
        **INTENT,
    },
)

# Under /keys, but not an exact mint POST. Admin stays on.
_NEAR_MINT_PATHS = (
    "/keys/services/",
    "/keys/servicesX",
    "/keys/services/extra",
    "/keys/Services",
    "/keys/intermediate/",
    "/keys/intermediate//revoke",
    "/keys/intermediate/a/b/revoke",
    "/keys/root",
)


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
    return root


@pytest.fixture()
def app(home: Any) -> FastAPI:
    return create_app(
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )


def _client(app: FastAPI, host: str) -> TestClient:
    return TestClient(app, client=(host, 5555))


def _guard_401(response: Any) -> None:
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["message"] == "unauthorized"
    assert body["error"]["type"] == "openvault_unauthenticated"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(app: FastAPI, host: str, service_id: str = "dms") -> tuple[TestClient, str]:
    client = _client(app, host)
    response = client.post("/keys/services", json={"service_id": service_id}, headers=INTENT)
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    assert isinstance(token, str) and token
    return client, token


def _issue(app: FastAPI, token: str, service_id: str = "dms") -> Any:
    return _client(app, "127.0.0.1").post(
        "/keys/intermediate",
        json={"service_id": service_id, "subject": service_id, "ttl_s": 300},
        headers=_bearer(token),
    )


def _jwks_kids(app: FastAPI) -> list[str]:
    body = _client(app, "127.0.0.1").get("/keys/jwks").json()
    return [str(item["kid"]) for item in body["keys"]]


def _audit_text(home: Any) -> str:
    path = home / "secret_audit.jsonl"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def test_mint_classification_is_exact_post_only() -> None:
    assert signing_mint_kind("POST", "/keys/services") == "services"
    assert signing_mint_kind("POST", "/keys/intermediate") == "intermediate"
    assert signing_mint_kind("POST", "/keys/intermediate/int-abc/revoke") == "revoke"
    assert signing_mint_kind("post", "/keys/services") == "services"
    for method in ("GET", "HEAD", "OPTIONS", "PUT", "DELETE"):
        assert signing_mint_kind(method, "/keys/services") == ""
        assert signing_mint_kind(method, "/keys/intermediate") == ""
        assert signing_mint_kind(method, "/keys/intermediate/int-abc/revoke") == ""
    for path in _NEAR_MINT_PATHS:
        assert signing_mint_kind("POST", path) == ""
    assert signing_mint_kind("POST", "/keys/intermediate/extra/revoke") == "revoke"
    assert signing_mint_kind("POST", "/keys/intermediate/a/b/revoke") == ""
    assert signing_mint_kind("POST", "/api/keys") == ""
    assert path_needs_admin("/keys/services")
    assert path_needs_admin("/keys/intermediate")
    assert path_needs_admin("/keys/jwks")
    assert path_needs_admin("/api/keys")
    assert path_needs_admin("/api/vault/status")
    assert path_needs_admin("/api/secrets")
    assert path_needs_admin("/api/apikeys")


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "::1", "10.128.0.3", "34.30.222.22", "::ffff:10.128.0.3"],
)
def test_services_mint_without_admin_from_allowlisted_peer(
    app: FastAPI, home: Any, host: str
) -> None:
    response = _client(app, host).post("/keys/services", json=_SERVICE, headers=INTENT)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["service_id"] == "dms"
    token = body["token"]
    assert token
    assert ADMIN_HEADER not in response.headers
    admin = ensure_admin_token()
    assert admin not in response.text
    assert token not in _audit_text(home)
    assert admin not in _audit_text(home)


def test_services_mint_env_cidr_without_admin(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENVAULT_SERVICES_ALLOW", "10.9.0.0/24")
    allowed = _client(app, "10.9.0.50").post("/keys/services", json=_SERVICE, headers=INTENT)
    assert allowed.status_code == 200, allowed.text
    blocked = _client(app, "10.9.1.50").post(
        "/keys/services", json={"service_id": "other"}, headers=INTENT
    )
    _guard_401(blocked)


def test_services_mint_requires_reveal(app: FastAPI) -> None:
    loop = _client(app, "127.0.0.1").post("/keys/services", json=_SERVICE)
    assert loop.status_code == 428
    peer = _client(app, "10.128.0.3").post("/keys/services", json=_SERVICE)
    assert peer.status_code == 428
    assert "X-OpenVault-Reveal" in peer.text


def test_remote_services_mint_without_credential_is_401(app: FastAPI) -> None:
    response = _client(app, _REMOTE).post("/keys/services", json=_SERVICE, headers=INTENT)
    _guard_401(response)


@pytest.mark.parametrize("headers", _SPOOF_HEADERS)
def test_spoofed_forwarded_or_host_is_not_a_mint_peer(
    app: FastAPI, headers: dict[str, str]
) -> None:
    response = _client(app, _REMOTE).post("/keys/services", json=_SERVICE, headers=headers)
    _guard_401(response)
    assert "token" not in response.json()


def test_forwarded_for_with_api_key_still_uses_the_socket(app: FastAPI, home: Any) -> None:
    _key_id, headers = issue_key(_client(app, "127.0.0.1"))
    remote = _client(app, "8.8.8.8")
    response = remote.post(
        "/keys/services",
        json=_SERVICE,
        headers={**INTENT, **headers, "X-Forwarded-For": "10.128.0.3", "Host": "127.0.0.1"},
    )
    assert response.status_code == 403
    assert "OPENVAULT_SERVICES_ALLOW" in response.json()["detail"]
    assert "token" not in response.json()
    ov_token = headers["Authorization"].removeprefix("Bearer ").strip()
    assert ov_token not in response.text
    assert ov_token not in _audit_text(home)


def test_bad_api_key_is_403_even_with_a_spoofed_prove_peer(app: FastAPI) -> None:
    response = _client(app, _REMOTE).post(
        "/keys/services",
        json=_SERVICE,
        headers={
            **INTENT,
            "Authorization": "Bearer ov_not-a-real-key",
            "X-Forwarded-For": "10.128.0.3",
            "Host": "10.128.0.3",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "openvault_forbidden"


def test_query_string_is_not_an_admin_credential(app: FastAPI) -> None:
    admin = ensure_admin_token()
    remote = _client(app, _REMOTE)
    response = remote.post(
        "/keys/services",
        params={"X-OpenVault-Admin": admin, "token": admin},
        json=_SERVICE,
        headers=INTENT,
    )
    _guard_401(response)
    assert admin not in response.text


@pytest.mark.parametrize("path", _NEAR_MINT_PATHS)
@pytest.mark.parametrize("host", ["127.0.0.1", _REMOTE])
def test_near_paths_still_need_admin(app: FastAPI, path: str, host: str) -> None:
    response = _client(app, host).post(path, json=_SERVICE, headers=INTENT)
    _guard_401(response)


def test_get_services_and_root_still_need_admin(app: FastAPI) -> None:
    client = _client(app, "127.0.0.1")
    for path in ("/keys/services", "/keys/root", "/keys/intermediate"):
        _guard_401(client.get(path))


def test_intermediate_and_revoke_without_admin(app: FastAPI, home: Any) -> None:
    client = _client(app, "127.0.0.1")
    registered = client.post("/keys/services", json=_SERVICE, headers=INTENT)
    assert registered.status_code == 200, registered.text
    service_token = registered.json()["token"]

    missing = client.post("/keys/intermediate", json=_SERVICE)
    assert missing.status_code == 401
    assert "Bearer" in missing.json()["detail"]

    issued = client.post(
        "/keys/intermediate",
        json={"service_id": "dms", "subject": "dms-manifest-signer", "ttl_s": 300},
        headers={"Authorization": f"Bearer {service_token}"},
    )
    assert issued.status_code == 200, issued.text
    payload = issued.json()
    private_key = payload["private_key"]
    kid = payload["kid"]
    assert private_key
    assert kid.startswith("int-")

    bare = client.post(f"/keys/intermediate/{kid}/revoke")
    assert bare.status_code == 401
    assert bare.json().get("lifecycle") != "revoked"
    assert kid in [item["kid"] for item in client.get("/keys/jwks").json()["keys"]]

    revoked = client.post(
        f"/keys/intermediate/{kid}/revoke",
        headers={"Authorization": f"Bearer {service_token}"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["lifecycle"] == "revoked"
    assert private_key not in revoked.text
    assert service_token not in revoked.text

    audit = _audit_text(home)
    assert service_token not in audit
    assert private_key not in audit
    admin = ensure_admin_token()
    assert admin not in audit
    assert admin not in issued.text


def test_allowlisted_peer_cannot_issue_or_revoke_without_a_credential(app: FastAPI) -> None:
    peer = _client(app, "10.128.0.3")
    issued = peer.post("/keys/intermediate", json=_SERVICE, headers=INTENT)
    _guard_401(issued)
    revoked = peer.post("/keys/intermediate/int-abc/revoke", headers=INTENT)
    _guard_401(revoked)


def test_unknown_kid_revoke_without_credential_is_401(app: FastAPI) -> None:
    """No credential is 401 from the route, not a 404 and not the admin-guard envelope."""
    response = _client(app, "127.0.0.1").post("/keys/intermediate/extra/revoke")
    assert response.status_code == 401
    body = response.json()
    assert "detail" in body
    assert body.get("error", {}).get("type") != "openvault_unauthenticated"
    assert body.get("lifecycle") != "revoked"


def test_loopback_revoke_without_credential_is_401(app: FastAPI) -> None:
    _client_dms, token = _register(app, "127.0.0.1")
    kid = _issue(app, token).json()["kid"]
    known = _client(app, "127.0.0.1").post(f"/keys/intermediate/{kid}/revoke")
    unknown = _client(app, "127.0.0.1").post("/keys/intermediate/int-missing/revoke")
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json()
    assert known.json().get("lifecycle") != "revoked"
    assert kid in _jwks_kids(app)


def test_loopback_revoke_bad_bearer_is_401(app: FastAPI) -> None:
    _client_dms, token = _register(app, "127.0.0.1")
    kid = _issue(app, token).json()["kid"]
    bad = _client(app, "127.0.0.1").post(
        f"/keys/intermediate/{kid}/revoke",
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert bad.status_code == 401
    assert token not in bad.text
    assert kid in _jwks_kids(app)


def test_loopback_revoke_with_service_bearer(app: FastAPI) -> None:
    client, token = _register(app, "127.0.0.1")
    kid = _issue(app, token).json()["kid"]
    revoked = client.post(f"/keys/intermediate/{kid}/revoke", headers=_bearer(token))
    assert revoked.status_code == 200
    assert revoked.json() == {"kid": kid, "lifecycle": "revoked"}
    assert token not in revoked.text
    assert kid not in _jwks_kids(app)


def test_loopback_revoke_with_admin(app: FastAPI) -> None:
    _client_dms, token = _register(app, "127.0.0.1")
    kid = _issue(app, token).json()["kid"]
    admin = ensure_admin_token()
    revoked = _client(app, "127.0.0.1").post(
        f"/keys/intermediate/{kid}/revoke",
        headers={ADMIN_HEADER: admin},
    )
    assert revoked.status_code == 200
    assert revoked.json()["lifecycle"] == "revoked"
    assert admin not in revoked.text
    assert token not in revoked.text
    assert kid not in _jwks_kids(app)


def test_loopback_revoke_wrong_admin_is_401(app: FastAPI) -> None:
    _client_dms, token = _register(app, "127.0.0.1")
    kid = _issue(app, token).json()["kid"]
    response = _client(app, "127.0.0.1").post(
        f"/keys/intermediate/{kid}/revoke",
        headers={ADMIN_HEADER: "not-the-admin-token"},
    )
    assert response.status_code == 401
    assert kid in _jwks_kids(app)


def test_loopback_revoke_unknown_kid_with_bearer_is_404(app: FastAPI) -> None:
    _client_dms, token = _register(app, "127.0.0.1")
    response = _client(app, "127.0.0.1").post(
        "/keys/intermediate/int-missing/revoke",
        headers=_bearer(token),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "intermediate key not found"


def test_allowlisted_peer_cannot_revoke_with_service_bearer(app: FastAPI) -> None:
    _client_dms, token = _register(app, "127.0.0.1")
    kid = _issue(app, token).json()["kid"]
    remote = _client(app, "10.128.0.3").post(
        f"/keys/intermediate/{kid}/revoke",
        headers=_bearer(token),
    )
    assert remote.status_code == 403
    assert kid in _jwks_kids(app)


@pytest.mark.parametrize("host", ["127.0.0.1", "10.128.0.3", "34.30.222.22"])
def test_reregister_reveal_only_does_not_rotate(app: FastAPI, host: str) -> None:
    client, token = _register(app, host)
    again = client.post("/keys/services", json=_SERVICE, headers=INTENT)
    assert again.status_code == 401
    assert "token" not in again.json()
    issued = _issue(app, token)
    assert issued.status_code == 200, issued.text


def test_reregister_with_current_bearer_rotates(app: FastAPI) -> None:
    client, token = _register(app, "34.30.222.22")
    rotated = client.post("/keys/services", json=_SERVICE, headers={**INTENT, **_bearer(token)})
    assert rotated.status_code == 200, rotated.text
    new_token = rotated.json()["token"]
    assert new_token != token
    stale = _issue(app, token)
    assert stale.status_code == 403
    fresh = _issue(app, new_token)
    assert fresh.status_code == 200, fresh.text


def test_reregister_with_admin_rotates(app: FastAPI) -> None:
    client, token = _register(app, "10.128.0.3")
    admin = ensure_admin_token()
    rotated = client.post(
        "/keys/services",
        json=_SERVICE,
        headers={**INTENT, ADMIN_HEADER: admin},
    )
    assert rotated.status_code == 200, rotated.text
    new_token = rotated.json()["token"]
    assert new_token != token
    assert admin not in rotated.text
    assert _issue(app, token).status_code == 403
    assert _issue(app, new_token).status_code == 200


def test_other_service_bearer_does_not_rotate(app: FastAPI) -> None:
    _client_dms, dms = _register(app, "10.128.0.3", "dms")
    _client_other, other = _register(app, "10.128.0.3", "other")
    denied = _client(app, "34.30.222.22").post(
        "/keys/services",
        json=_SERVICE,
        headers={**INTENT, **_bearer(other)},
    )
    assert denied.status_code == 401
    assert "token" not in denied.json()
    assert other not in denied.text
    assert _issue(app, dms).status_code == 200


def test_second_service_first_mint_stays_reveal_only(app: FastAPI) -> None:
    _register(app, "34.30.222.22", "dms")
    other = _client(app, "34.30.222.22").post(
        "/keys/services",
        json={"service_id": "other"},
        headers=INTENT,
    )
    assert other.status_code == 200, other.text
    assert other.json()["service_id"] == "other"
    assert other.json()["token"]


def test_reregister_without_reveal_does_not_rotate(app: FastAPI) -> None:
    client, token = _register(app, "10.128.0.3")
    again = client.post("/keys/services", json=_SERVICE)
    assert again.status_code == 428
    assert _issue(app, token).status_code == 200


def test_allowlisted_bad_ov_key_is_still_403(app: FastAPI) -> None:
    response = _client(app, "34.30.222.22").post(
        "/keys/services",
        json=_SERVICE,
        headers={**INTENT, "Authorization": "Bearer ov_not-a-real-key"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "openvault_forbidden"
    assert "token" not in response.text


def test_unlisted_remote_service_bearer_cannot_register(app: FastAPI) -> None:
    _client_dms, token = _register(app, "127.0.0.1")
    remote = _client(app, _REMOTE).post(
        "/keys/services",
        json=_SERVICE,
        headers={**INTENT, **_bearer(token)},
    )
    assert remote.status_code == 403
    assert token not in remote.text
    assert _issue(app, token).status_code == 200


def test_remote_intermediate_and_revoke_without_credential_is_401(app: FastAPI) -> None:
    remote = _client(app, _REMOTE)
    _guard_401(remote.post("/keys/intermediate", json=_SERVICE, headers=INTENT))
    _guard_401(remote.post("/keys/intermediate/int-abc/revoke"))


def test_admin_routes_and_secret_reveal_still_need_admin(app: FastAPI) -> None:
    client = _client(app, "127.0.0.1")
    _guard_401(client.get("/api/keys"))
    _guard_401(client.get("/api/vault/status"))
    _guard_401(client.get("/api/secrets"))
    _guard_401(client.get("/api/apikeys"))
    reveal = client.get("/api/keys/not-a-key/secret", headers=INTENT)
    _guard_401(reveal)
    remote = _client(app, _REMOTE)
    _guard_401(remote.get("/api/keys"))
    _guard_401(remote.post("/api/apikeys", json={"label": "nope", "tier": "free"}))


def test_jwks_reads_stay_public(app: FastAPI) -> None:
    remote = _client(app, _REMOTE)
    alt = remote.get("/keys/jwks")
    well = remote.get("/.well-known/jwks.json")
    assert alt.status_code == 200
    assert well.status_code == 200
    assert alt.content == well.content
    parsed = alt.json()
    assert parsed["keys"]
    assert "private_key" not in alt.text
    for method in ("HEAD", "OPTIONS"):
        mirrored = remote.request(method, "/keys/jwks")
        assert mirrored.status_code != 401
    _guard_401(remote.request("POST", "/keys/jwks"))


def test_raw_path_equality_does_not_treat_traversal_as_mint(app: FastAPI) -> None:
    """scope path is what the guard reads. No unquote and no slash collapse."""
    status, body = _raw(
        app,
        "POST",
        "/keys/services/../services",
        host="10.128.0.3",
        headers={**INTENT, "content-type": "application/json"},
        payload=_SERVICE,
    )
    assert status == 401
    parsed = json.loads(body)
    assert parsed["error"]["type"] == "openvault_unauthenticated"
    assert b'"token"' not in body


def _raw(
    app: FastAPI,
    method: str,
    path: str,
    *,
    host: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    sent: list[dict[str, Any]] = []
    raw_body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    sent_body = False

    async def receive() -> dict[str, Any]:
        nonlocal sent_body
        if sent_body:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent_body = True
        return {"type": "http.request", "body": raw_body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    encoded = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": encoded,
        "client": (host, 5555),
        "server": ("testserver", 80),
        "root_path": "",
    }
    asyncio.run(app(scope, receive, send))
    status = next(int(item["status"]) for item in sent if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return status, body
