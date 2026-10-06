"""A root_path prefix must not skip HttpGuard.

Starlette routes ``get_route_path(scope)``, which strips ``root_path`` from
``scope["path"]``. The guard used to read ``request.url.path`` and miss
``/api`` and ``/keys``, so the router still served the route.

Fixtures only. No admin header is injected.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from conftest import inject_admin_credential
from fastapi import FastAPI
from starlette._utils import get_route_path

from openmw.openvault.app import create_app

INTENT = {"X-OpenVault-Reveal": "intentional"}
_REMOTE = "203.0.113.10"
_SERVICE = {"service_id": "dms"}


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


def _raw(
    app: FastAPI,
    method: str,
    path: str,
    *,
    root_path: str = "",
    host: str = _REMOTE,
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
        "root_path": root_path,
    }
    asyncio.run(app(scope, receive, send))
    status = next(int(item["status"]) for item in sent if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return status, body


def _assert_unauthenticated(status: int, body: bytes) -> None:
    assert status == 401
    parsed = json.loads(body)
    assert parsed["error"]["type"] == "openvault_unauthenticated"
    assert b'"token"' not in body


@pytest.mark.parametrize(
    ("method", "path", "root_path"),
    [
        ("GET", "/mounted/api/keys", "/mounted"),
        ("GET", "//api/keys", "/"),
        ("GET", "/mounted/api/keys/", "/mounted"),
        ("GET", "/mounted/api/keys//", "/mounted"),
        ("GET", "/mounted/api/vault/status", "/mounted"),
        ("GET", "/api/healthz/../keys", ""),
        ("GET", "/api/healthzX/api/keys", "/api/healthzX"),
        ("POST", "/mounted/keys/services", "/mounted"),
        ("POST", "//keys/services", "/"),
        ("POST", "/mounted/keys/services/", "/mounted"),
        ("POST", "/mounted/keys/services//", "/mounted"),
        ("POST", "/mounted/keys/intermediate", "/mounted"),
        ("POST", "//keys/intermediate", "/"),
        ("POST", "/mounted/keys/intermediate/", "/mounted"),
        ("POST", "/mounted/keys/jwks", "/mounted"),
        ("GET", "/mounted/keys/jwks/", "/mounted"),
        ("GET", "/mounted/keys/jwksX", "/mounted"),
    ],
)
def test_root_path_prefix_does_not_skip_auth(
    app: FastAPI, method: str, path: str, root_path: str
) -> None:
    headers = {"content-type": "application/json", **INTENT} if method == "POST" else None
    routed = get_route_path({"type": "http", "path": path, "root_path": root_path})
    mint_paths = ("/keys/services", "/keys/intermediate")
    payload = _SERVICE if method == "POST" and routed in mint_paths else None
    unprefixed, _unprefixed_body = _raw(app, method, routed, headers=headers, payload=payload)
    status, body = _raw(
        app,
        method,
        path,
        root_path=root_path,
        headers=headers,
        payload=payload,
    )
    assert status in (401, 403), (path, root_path, status, body[:200])
    _assert_unauthenticated(status, body)
    assert status == unprefixed


def test_doubled_slash_services_from_remote_is_401(app: FastAPI) -> None:
    status, body = _raw(
        app,
        "POST",
        "//keys/services",
        root_path="/",
        headers={"content-type": "application/json", **INTENT},
        payload=_SERVICE,
    )
    _assert_unauthenticated(status, body)


def test_loopback_first_mint_still_200_behind_root_path(app: FastAPI) -> None:
    status, body = _raw(
        app,
        "POST",
        "/mounted/keys/services",
        root_path="/mounted",
        host="127.0.0.1",
        headers={"content-type": "application/json", **INTENT},
        payload=_SERVICE,
    )
    assert status == 200, body
    parsed = json.loads(body)
    assert parsed["service_id"] == "dms"
    assert parsed["token"]


def test_allowlisted_peer_mint_still_200_behind_root_path(app: FastAPI) -> None:
    status, body = _raw(
        app,
        "POST",
        "/mounted/keys/services",
        root_path="/mounted",
        host="10.128.0.3",
        headers={"content-type": "application/json", **INTENT},
        payload=_SERVICE,
    )
    assert status == 200, body
    assert json.loads(body)["service_id"] == "dms"


def test_prefixed_jwks_read_stays_public(app: FastAPI) -> None:
    """GET stays 200. HEAD and OPTIONS stay on the public side (not 401)."""
    for method in ("GET", "HEAD", "OPTIONS"):
        plain, plain_body = _raw(app, method, "/keys/jwks")
        status, body = _raw(app, method, "/mounted/keys/jwks", root_path="/mounted")
        assert plain != 401, method
        assert status == plain, method
        assert body == plain_body
        if method == "GET":
            assert status == 200
            assert b"root-" in body
    doubled, doubled_body = _raw(app, "GET", "//keys/jwks", root_path="/")
    _plain_get, plain_get_body = _raw(app, "GET", "/keys/jwks")
    assert doubled == 200
    assert doubled_body == plain_get_body


def test_prefixed_healthz_stays_public(app: FastAPI) -> None:
    plain, plain_body = _raw(app, "GET", "/api/healthz")
    status, body = _raw(app, "GET", "/mounted/api/healthz", root_path="/mounted")
    assert plain == 200
    assert status == 200
    assert body == plain_body
    assert json.loads(body)["status"] == "ok"


def test_missing_route_path_helper_fails_closed(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ImportError must not skip auth and must not stop the process from starting.

    ``app`` is already built. The helper is loaded per request, not at import.
    """

    def _missing() -> Any:
        raise ImportError("starlette._utils.get_route_path")

    monkeypatch.setattr("openmw.openvault.vault.http_guard._load_get_route_path", _missing)
    for method, path, root_path in (
        ("GET", "/api/keys", ""),
        ("GET", "/mounted/api/keys", "/mounted"),
        ("GET", "/api/healthz", ""),
        ("GET", "/keys/jwks", ""),
        ("POST", "/keys/services", ""),
    ):
        headers = {"content-type": "application/json", **INTENT} if method == "POST" else None
        payload = _SERVICE if method == "POST" else None
        status, body = _raw(
            app,
            method,
            path,
            root_path=root_path,
            headers=headers,
            payload=payload,
        )
        _assert_unauthenticated(status, body)
        assert b"root-" not in body
        assert b'"token"' not in body
