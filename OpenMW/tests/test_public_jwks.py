"""Public ``GET /keys/jwks`` (OpenVault #112).

Red on ``68514a7214784632248a87a62822ba6acbf777fe`` (deploy 2026-10-02
02:13 MYT): ``GET /keys/jwks`` without ``X-OpenVault-Admin`` was 401.
The poller recorded 376x 401. ``/.well-known/jwks.json`` was already
public. ``/keys/jwks`` is the published ``jwks_alt``.

The guard compares ``request.url.path`` with equality. No prefix, no
regex, no second normaliser. This module sends no admin token.

R-0007, each control fails on its own:

| mutation | test |
|---|---|
| drop the exemption | ``test_get_is_the_well_known_document`` |
| prefix or regex instead of equality | ``test_near_paths_are_not_the_public_document`` |
| unquote, casefold, or strip a slash | ``test_encoded_case_and_traversal_stay_shut`` |
| exempt POST/PUT/DELETE | ``test_mutations_are_401_not_405`` |
| trust loopback for every ``/keys`` route | ``test_loopback_still_needs_admin_off_this_path`` |
| honor a query token | ``test_query_token_is_not_a_credential`` |
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from conftest import inject_admin_credential
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from openmw.openvault.app import create_app
from openmw.openvault.vault.admin_token import ensure_admin_token
from openmw.openvault.vault.trust import TrustStore

# RFC 7517/7518 private members, plus the names this vault uses for a secret half.
_PRIVATE_JWK_MEMBERS = frozenset(
    {"d", "p", "q", "dp", "dq", "qi", "oth", "k", "priv", "private_key"}
)

_CLOSED_GETS = (
    "/keys/jwksX",
    "/keys/jwks/",
    "/keys/jwks.json",
    "/keys/jwks/extra",
    "/keys/JWKS",
    "/api/keys",
    "/api/keys/quota",
    "/keys/services",
    "/keys/root",
)

# scope["path"] strings. ``request.url.path`` does not unquote or collapse these.
_RAW_STILL_401 = (
    "/keys/jwksX",
    "/keys/jwks/",
    "/keys/jwks/../../api/keys",
    "/keys/jwks/../services",
    "/keys/./jwks",
    "/keys/jwks/..",
    "/keys/%6awks",
    "/keys/jwks%2F",
    "/keys/jwks%2f..%2fservices",
    "/keys/%2e%2e/jwks",
    "/keys/jwks%00",
    "/keys/jwks%20",
    "/keys/jwks/%2e%2e/api/keys",
    "/keys/JWKS",
)

_RAW_NOT_THE_DOCUMENT = (
    "/KEYS/jwks",
    "//keys/jwks",
    "/%6beys/jwks",
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


def _raw(
    app: FastAPI,
    method: str,
    path: str,
    *,
    host: str = "203.0.113.10",
    query: bytes = b"",
) -> tuple[int, bytes]:
    """One ASGI call whose scope path is the string ``http_guard`` reads."""
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query,
        "headers": [],
        "client": (host, 5555),
        "server": ("testserver", 80),
        "root_path": "",
    }
    asyncio.run(app(scope, receive, send))
    status = next(int(item["status"]) for item in sent if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return status, body


def _assert_public_document(body: bytes) -> None:
    document = JSONResponse(TrustStore().jwks()).body
    assert body == document
    parsed = json.loads(body)
    keys = parsed["keys"]
    assert keys
    assert any(str(jwk.get("kid", "")).startswith("root-") for jwk in keys)
    for jwk in keys:
        assert _PRIVATE_JWK_MEMBERS.isdisjoint(jwk)
    lowered = body.lower()
    assert b"private" not in lowered
    assert b"begin " not in lowered


def test_get_is_the_well_known_document(app: FastAPI) -> None:
    """Remote and loopback, no admin header and no ov_ key."""
    first = _client(app, "203.0.113.10")
    alt = first.get("/keys/jwks")
    well = first.get("/.well-known/jwks.json")
    assert alt.status_code == 200
    assert well.status_code == 200
    assert alt.content == well.content
    _assert_public_document(alt.content)
    assert alt.headers.get("access-control-allow-origin") == "*"
    for host in ("127.0.0.1", "::1"):
        other = _client(app, host).get("/keys/jwks")
        assert other.status_code == 200
        assert other.content == alt.content


def test_head_and_options_mirror_well_known(app: FastAPI) -> None:
    client = _client(app, "203.0.113.10")
    for method in ("HEAD", "OPTIONS"):
        known = client.request(method, "/.well-known/jwks.json")
        alt = client.request(method, "/keys/jwks")
        assert alt.status_code == known.status_code
        assert alt.content == known.content
        assert alt.status_code != 401


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_mutations_are_401_not_405(app: FastAPI, method: str) -> None:
    response = _client(app, "203.0.113.10").request(method, "/keys/jwks")
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "openvault_unauthenticated"


@pytest.mark.parametrize("path", _CLOSED_GETS)
@pytest.mark.parametrize("host", ["203.0.113.10", "127.0.0.1"])
def test_near_paths_are_not_the_public_document(app: FastAPI, path: str, host: str) -> None:
    response = _client(app, host).get(path)
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "unauthorized"


@pytest.mark.parametrize("path", _RAW_STILL_401)
def test_encoded_case_and_traversal_stay_shut(app: FastAPI, path: str) -> None:
    status, body = _raw(app, "GET", path)
    assert status == 401, path
    assert b"root-" not in body
    assert b"unauthorized" in body


@pytest.mark.parametrize("path", _RAW_NOT_THE_DOCUMENT)
def test_case_and_double_slash_are_not_the_jwks(app: FastAPI, path: str) -> None:
    status, body = _raw(app, "GET", path)
    assert status != 200, path
    assert b"root-" not in body
    public, public_body = _raw(app, "GET", "/keys/jwks")
    assert public == 200
    assert body != public_body


def test_query_token_is_not_a_credential(app: FastAPI) -> None:
    token = ensure_admin_token()
    query = f"token={token}&access_token={token}&X-OpenVault-Admin={token}".encode()
    for path in ("/api/keys", "/api/keys/quota", "/keys/services"):
        status, body = _raw(app, "GET", path, host="127.0.0.1", query=query)
        assert status == 401, path
        assert token.encode() not in body
    status, body = _raw(app, "GET", "/keys/jwks", query=query)
    assert status == 200
    plain, plain_body = _raw(app, "GET", "/keys/jwks")
    assert plain == 200
    assert body == plain_body
    assert token.encode() not in body


def test_loopback_still_needs_admin_off_this_path(app: FastAPI) -> None:
    client = _client(app, "127.0.0.1")
    assert client.get("/keys/jwks").status_code == 200
    for path in ("/api/keys", "/api/keys/quota", "/keys/services", "/keys/root"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["message"] == "unauthorized"
