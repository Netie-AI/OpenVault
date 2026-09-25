"""Fail-closed HTTP guard for every ``/api/*`` and ``/keys/*`` route.

A request is admitted only when it presents a valid issued OpenVault API key
(the same ``Authorization: Bearer`` / ``X-API-Key`` verification FreeRoute
uses) or the socket peer is loopback. ``OPENVAULT_REQUIRE_API_KEY`` cannot
open a remote path: unset, false, or true, the guard still runs.

The peer is ``request.client.host`` after the socket accept. Forwarded and
Host headers are never read.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from starlette.requests import Request
from starlette.responses import JSONResponse

from openmw.openvault.vault.auth import bearer_token

#: The only unauthenticated ``/api/*`` path. Length 1 is a contract test.
AUTH_ALLOWLIST: frozenset[str] = frozenset({"/api/healthz"})

_GUARDED_PREFIXES: tuple[str, ...] = ("/api/", "/keys/")
_DOCS_TRUTH = frozenset({"1", "true", "yes", "on"})
_LOOPBACK_PEERS: frozenset[str] = frozenset({"127.0.0.1", "::1"})


class _KeyStore(Protocol):
    def verify(self, token: str) -> object: ...


def _missing() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": {"message": "unauthorized", "type": "openvault_unauthenticated"}},
    )


def _bad() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"error": {"message": "unauthorized", "type": "openvault_forbidden"}},
    )


def dev_docs_enabled() -> bool:
    """``/docs``, ``/redoc``, ``/openapi.json`` stay off unless this is set."""
    return (os.environ.get("OPENVAULT_DEV_DOCS") or "").strip().lower() in _DOCS_TRUTH


def path_is_guarded(path: str) -> bool:
    if path in AUTH_ALLOWLIST:
        return False
    return path.startswith(_GUARDED_PREFIXES)


def peer_is_loopback(host: str) -> bool:
    """True only for a loopback socket peer. Never a header value."""
    value = (host or "").strip()
    if value.startswith("[") and "]" in value:
        value = value[1 : value.find("]")]
    if value.lower().startswith("::ffff:"):
        value = value[len("::ffff:") :]
    return value in _LOOPBACK_PEERS


def refuse_if_unauthorised(request: Request, *, api_keys: _KeyStore) -> JSONResponse | None:
    """Return a 401/403 response to send, or None to let the request through."""
    path = request.url.path
    if not path_is_guarded(path):
        return None

    client = request.client
    host = client.host if client is not None else ""
    if peer_is_loopback(host):
        return None

    token = bearer_token(request)
    if token:
        record = api_keys.verify(token)
        if record is None:
            return _bad()
        return None
    return _missing()


class HttpGuardMiddleware:
    """Pure ASGI wrapper so streaming routes are not buffered."""

    def __init__(self, app: Any, api_keys: _KeyStore) -> None:
        self.app = app
        self.api_keys = api_keys

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        refused = refuse_if_unauthorised(request, api_keys=self.api_keys)
        if refused is not None:
            await refused(scope, receive, send)
            return
        await self.app(scope, receive, send)


__all__ = [
    "AUTH_ALLOWLIST",
    "HttpGuardMiddleware",
    "dev_docs_enabled",
    "path_is_guarded",
    "peer_is_loopback",
    "refuse_if_unauthorised",
]
