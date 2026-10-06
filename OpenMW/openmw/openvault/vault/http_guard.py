"""Fail-closed HTTP guard for every ``/api/*`` and ``/keys/*`` route.

Applies to every HTTP method (GET, POST, PUT, PATCH, DELETE, and any other
method the route declares). A request is admitted only when it presents a
valid issued OpenVault API key (the same ``Authorization: Bearer`` /
``X-API-Key`` verification FreeRoute uses) or the socket peer is loopback.
``OPENVAULT_REQUIRE_API_KEY`` cannot open a remote path: unset, false, or
true, the guard still runs.

Key and secret management routes, and ``/keys`` routes, also require the
separate admin credential in ``X-OpenVault-Admin``. Loopback does not skip
that check. The admin token is not an ``ov_`` key. Two exceptions:

* ``GET``, ``HEAD``, and ``OPTIONS`` on the exact path ``/keys/jwks`` are
  public, same as ``/.well-known/jwks.json``.
* ``POST /keys/services``, ``POST /keys/intermediate``, and
  ``POST /keys/intermediate/{kid}/revoke`` do not require the admin header
  (#126). They stay on this guard. ``POST /keys/services`` also admits a
  socket peer on the #52 allowlist when no API key is presented, so the
  route can apply its peer check and ``X-OpenVault-Reveal: intentional``.
  A presented ``ov_`` API key is still verified. A non-``ov_`` bearer from an
  allowlisted peer on ``POST /keys/services`` is left for the route, so a
  re-register can present the current service token. Other methods and near
  paths stay on the admin gate.

The peer is ``request.client.host`` after the socket accept. Forwarded and
Host headers are never read.

``POST /api/apikeys/verify`` is the one extra admission. The path stays an
admin route in ``path_needs_admin`` (every other method still needs
``X-OpenVault-Admin``). This guard admits that exact POST when the socket
peer is loopback or the services allowlist, and the caller presents either
the admin token or a Bearer that passes ``verify_service`` for a service_id
listed in ``OPENVAULT_VERIFY_SERVICES``. The list defaults to empty, so a
service Bearer is refused until an operator sets it. Loopback alone is not
enough. No other ``/api/apikeys`` path accepts a service Bearer.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse

from openmw.openvault.vault.admin_token import (
    ADMIN_HEADER,
    admin_token_matches,
    path_needs_admin,
)
from openmw.openvault.vault.api_keys import TOKEN_PREFIX
from openmw.openvault.vault.auth import bearer_token

#: The only unauthenticated ``/api/*`` path. Length 1 is a contract test.
AUTH_ALLOWLIST: frozenset[str] = frozenset({"/api/healthz"})

_GUARDED_PREFIXES: tuple[str, ...] = ("/api/", "/keys/")
_DOCS_TRUTH = frozenset({"1", "true", "yes", "on"})
_LOOPBACK_PEERS: frozenset[str] = frozenset({"127.0.0.1", "::1"})
# Published jwks_alt. Equality on request.url.path. Not a prefix and not a regex.
_PUBLIC_JWKS_PATH = "/keys/jwks"
_PUBLIC_JWKS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Signing mint (#126). Equality on request.url.path, POST only. Not a prefix.
_SERVICES_MINT_PATH = "/keys/services"
_INTERMEDIATE_MINT_PATH = "/keys/intermediate"
_REVOKE_MINT_PATH = re.compile(r"/keys/intermediate/[^/]+/revoke\Z")
# Exact path. Not a prefix. Other methods stay on the admin gate.
_APIKEY_VERIFY_PATH = "/api/apikeys/verify"
VERIFY_SERVICES_ENV = "OPENVAULT_VERIFY_SERVICES"

log = structlog.get_logger()


@dataclass(frozen=True)
class ApikeyVerifyDecision:
    """Admission for ``POST /api/apikeys/verify``.

    ``caller_id`` is ``admin`` or ``service:<id>`` when admitted, else empty.
    It is a rate-limit bucket name, never a raw key or bearer.
    """

    admitted: bool
    status_code: int
    caller_id: str


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


def signing_mint_kind(method: str, path: str) -> str:
    """``services``, ``intermediate``, ``revoke``, or empty.

    ``path`` is ``request.url.path`` with no further normalisation. POST only.
    A trailing slash, an extra segment, a different method, or a different
    case is not a mint route and stays on the admin gate.
    """
    if method.upper() != "POST":
        return ""
    if path == _SERVICES_MINT_PATH:
        return "services"
    if path == _INTERMEDIATE_MINT_PATH:
        return "intermediate"
    if _REVOKE_MINT_PATH.fullmatch(path) is not None:
        return "revoke"
    return ""


def _socket_peer_may_mint_service(host: str) -> bool:
    """True when ``host`` is on the #52 services allowlist.

    ``host`` is ``request.client.host``. The allowlist helper does not read
    ``X-Forwarded-For`` or ``Host``. Import is deferred because ``app`` imports
    this module. A failure to load the allowlist refuses the peer.
    """
    try:
        from openmw.openvault.app import _host_in_services_allow
    except ImportError:
        log.warning("services_allow_import_failed")
        return False
    return bool(_host_in_services_allow(host))


def apikey_verify_post(method: str, path: str) -> bool:
    """True only for exact ``POST /api/apikeys/verify``.

    ``path`` is ``request.url.path`` with no further normalisation. A trailing
    slash, a different method, or a different case stays on the admin gate.
    """
    return method.upper() == "POST" and path == _APIKEY_VERIFY_PATH


def verify_service_allowlist() -> tuple[str, ...]:
    """Service ids allowed to call verify. Empty unless the env is set.

    Comma-separated. Whitespace around each id is ignored. Default empty means
    only ``X-OpenVault-Admin`` can verify. Prove sets this to ``cortex``.
    """
    raw = (os.environ.get(VERIFY_SERVICES_ENV) or "").strip()
    if not raw:
        return ()
    seen: list[str] = []
    for part in raw.split(","):
        item = part.strip()
        if item and item not in seen:
            seen.append(item)
    return tuple(seen)


def _presented_service_bearer(request: Request) -> str:
    """Authorization Bearer value, or empty. ``X-API-Key`` is not this credential."""
    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _admin_presented(request: Request) -> bool:
    presented = (request.headers.get(ADMIN_HEADER) or "").strip()
    if not presented:
        return False
    return admin_token_matches(presented)


def _allowlisted_service_id(request: Request, token: str) -> str:
    """Return the allowlisted service_id this bearer matches, or empty.

    Every id on the list is checked, including after a match, so the list
    length does not change with which id hit. The bearer is never logged.
    """
    allowed = verify_service_allowlist()
    if not allowed or not token:
        return ""
    from openmw.openvault.vault.trust import TrustStore

    seal = getattr(request.app.state, "seal", None)
    store = TrustStore(seal=seal)
    matched = ""
    for service_id in allowed:
        if store.verify_service(service_id, token):
            matched = service_id
    return matched


def _verify_peer_allowed(request: Request) -> bool:
    """Socket peer is loopback or the services allowlist.

    Reads ``request.client.host`` only. ``X-Forwarded-For`` and ``Host`` are
    not consulted.
    """
    client = request.client
    host = client.host if client is not None else ""
    return _socket_peer_may_mint_service(host)


def decide_apikey_verify(request: Request) -> ApikeyVerifyDecision:
    """Admit admin or an allowlisted service bearer from an allowed peer.

    Peer is checked first so a remote caller cannot learn whether a bearer
    matches. No credential, an unlisted service, a wrong bearer, and a revoked
    service are 401. A peer that is not loopback and not on the services
    allowlist is 403.
    """
    if not _verify_peer_allowed(request):
        client = request.client
        host = client.host if client is not None else ""
        log.warning("apikey_verify_rejected", reason="peer", client=host)
        return ApikeyVerifyDecision(False, 403, "")
    if _admin_presented(request):
        return ApikeyVerifyDecision(True, 200, "admin")
    token = _presented_service_bearer(request)
    if not token:
        log.info("apikey_verify_rejected", reason="credential")
        return ApikeyVerifyDecision(False, 401, "")
    service_id = _allowlisted_service_id(request, token)
    if not service_id:
        log.info("apikey_verify_rejected", reason="service")
        return ApikeyVerifyDecision(False, 401, "")
    return ApikeyVerifyDecision(True, 200, f"service:{service_id}")


def _refuse_apikey_verify(request: Request) -> JSONResponse | None:
    decision = decide_apikey_verify(request)
    if decision.admitted:
        return None
    if decision.status_code == 403:
        return _bad()
    return _missing()


def _public_jwks_read(method: str, path: str) -> bool:
    """True only for the published JWKS alt.

    ``path`` is ``request.url.path`` with no further normalisation. HEAD and
    OPTIONS stay on this side so they match ``/.well-known/jwks.json``. Any
    other method stays on the admin gate and answers 401 before a 405.
    """
    return method.upper() in _PUBLIC_JWKS_METHODS and path == _PUBLIC_JWKS_PATH


def _defer_non_api_bearer(mint: str, host: str, token: str) -> bool:
    """True when the route, not this guard, must judge ``token``.

    ``POST /keys/services`` from an allowlisted socket may carry the current
    service bearer. That value is not an ``ov_`` API key. Failed ``ov_`` keys
    stay 403. Other mint routes and unlisted peers do not get this pass.
    """
    if mint != "services" or token.startswith(TOKEN_PREFIX):
        return False
    return _socket_peer_may_mint_service(host)


def peer_is_loopback(host: str) -> bool:
    """True only for a loopback socket peer. Never a header value."""
    value = (host or "").strip()
    if value.startswith("[") and "]" in value:
        value = value[1 : value.find("]")]
    if value.lower().startswith("::ffff:"):
        value = value[len("::ffff:") :]
    return value in _LOOPBACK_PEERS


def refuse_if_unauthorised(request: Request, *, api_keys: _KeyStore) -> JSONResponse | None:
    """Return a 401/403 response to send, or None to let the request through.

    Method is ignored on every path except the public JWKS alt (GET, HEAD,
    OPTIONS on exact ``/keys/jwks``), the three POST signing-mint routes, and
    exact ``POST /api/apikeys/verify``. Other methods, including
    POST/PUT/DELETE on ``/keys/jwks`` and every other ``/api/apikeys`` path,
    are guarded the same way as every other admin route.
    """
    path = request.url.path
    # Same path string as the admin check below. Do not unquote or casefold.
    if _public_jwks_read(request.method, path):
        return None
    if apikey_verify_post(request.method, path):
        return _refuse_apikey_verify(request)

    mint = signing_mint_kind(request.method, path)
    if not mint and path_needs_admin(path):
        presented = (request.headers.get(ADMIN_HEADER) or "").strip()
        if not admin_token_matches(presented):
            return _missing()

    if not path_is_guarded(path):
        return None

    client = request.client
    host = client.host if client is not None else ""
    if peer_is_loopback(host):
        return None

    token = bearer_token(request)
    if token:
        record = api_keys.verify(token)
        if record is not None:
            return None
        if _defer_non_api_bearer(mint, host, token):
            return None
        return _bad()

    # No credential. Only the services mint admits an allowlisted socket peer.
    # Intermediate issue and revoke stay 401 here; their handlers are loopback.
    if mint == "services" and _socket_peer_may_mint_service(host):
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
    "VERIFY_SERVICES_ENV",
    "ApikeyVerifyDecision",
    "HttpGuardMiddleware",
    "apikey_verify_post",
    "decide_apikey_verify",
    "dev_docs_enabled",
    "path_is_guarded",
    "path_needs_admin",
    "peer_is_loopback",
    "refuse_if_unauthorised",
    "signing_mint_kind",
    "verify_service_allowlist",
]
