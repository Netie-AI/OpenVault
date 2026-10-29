"""Fail-closed HTTP guard for every ``/api/*`` and ``/keys/*`` route.

Applies to every HTTP method (GET, POST, PUT, PATCH, DELETE, and any other
method the route declares). A request is admitted only when it presents a
valid issued OpenVault API key (the same ``Authorization: Bearer`` /
``X-API-Key`` verification FreeRoute uses) or the socket peer is loopback.
``OPENVAULT_REQUIRE_API_KEY`` cannot open a remote path: unset, false, or
true, the guard still runs.

Key and secret management routes, and ``/keys`` routes, also require the
separate admin credential in ``X-OpenVault-Admin``. Loopback does not skip
that check. The admin token is not an ``ov_`` key. Exceptions:

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

``POST /api/keys/leases`` and ``POST /api/keys/leases/redeem`` are the lease
exception. Exact path, POST only. Other methods stay on the admin gate.
Admission is a Bearer that ``verify_service`` accepts. ``X-OpenVault-Admin``
does not admit these two POSTs, alone or instead of that Bearer. The peer
must be loopback, or the ASGI scheme must be https while
``LEASE_TLS_VERIFY`` is on. ``LEASE_TLS_VERIFY`` is not an env flag.
Forwarded headers are not a scheme and not a peer.

The path compared here is the path Starlette routes, not ``request.url.path``.
``root_path`` is stripped the same way ``starlette._utils.get_route_path``
strips it. A prefix on ``scope["path"]`` used to miss ``/api`` and ``/keys``
while the router still served the route. That helper is private. If it cannot
be imported, this guard returns 401. It does not skip auth, and the import
is not at module load, so startup still succeeds. The verify admission uses
that same routed path. A missing helper is 401 there too.
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
from openmw.openvault.vault.lease import LEASE_MINT_PATH, LEASE_REDEEM_PATH

#: The only unauthenticated ``/api/*`` path. Length 1 is a contract test.
AUTH_ALLOWLIST: frozenset[str] = frozenset({"/api/healthz"})

_GUARDED_PREFIXES: tuple[str, ...] = ("/api/", "/keys/")
_DOCS_TRUTH = frozenset({"1", "true", "yes", "on"})
_LOOPBACK_PEERS: frozenset[str] = frozenset({"127.0.0.1", "::1"})
# Published jwks_alt. Equality on the routed path. Not a prefix and not a regex.
_PUBLIC_JWKS_PATH = "/keys/jwks"
_PUBLIC_JWKS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Signing mint (#126). Equality on the routed path, POST only. Not a prefix.
_SERVICES_MINT_PATH = "/keys/services"
_INTERMEDIATE_MINT_PATH = "/keys/intermediate"
_REVOKE_MINT_PATH = re.compile(r"/keys/intermediate/[^/]+/revoke\Z")
# Exact path. Not a prefix. Other methods stay on the admin gate.
_APIKEY_VERIFY_PATH = "/api/apikeys/verify"
VERIFY_SERVICES_ENV = "OPENVAULT_VERIFY_SERVICES"
#: Non-loopback lease calls are https only, and only while this stays true.
#: Not read from the environment. There is no verify=False client on this path.
LEASE_TLS_VERIFY = True

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


def _load_get_route_path() -> Any:
    """The router helper. ImportError if Starlette moves this private name."""
    from starlette._utils import get_route_path

    return get_route_path


def route_path(request: Request) -> str | None:
    """Path the router matches, or None when that helper cannot be loaded.

    Same strip as ``starlette.routing`` (``get_route_path``). ``request.url.path``
    keeps a ``root_path`` prefix, so ``/mounted/api/keys`` with ``root_path``
    ``/mounted`` used to skip this guard and still hit ``GET /api/keys``.
    No unquote and no slash collapse beyond that strip. None fails closed.
    """
    try:
        get_route_path = _load_get_route_path()
    except ImportError:
        log.warning("route_path_helper_missing")
        return None
    routed = get_route_path(request.scope)
    if not isinstance(routed, str):
        log.warning("route_path_helper_bad")
        return None
    return routed


def signing_mint_kind(method: str, path: str) -> str:
    """``services``, ``intermediate``, ``revoke``, or empty.

    ``path`` is the routed path (see ``route_path``) with no further
    normalisation. POST only. A trailing slash, an extra segment, a different
    method, or a different case is not a mint route and stays on the admin gate.
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


def lease_post(method: str, path: str) -> str:
    """``mint``, ``redeem``, or empty.

    Exact POST only. A trailing slash, another method, or an extra segment
    stays on the admin gate. The kid and the ref are not part of ``path``.
    """
    if method.upper() != "POST":
        return ""
    if path == LEASE_MINT_PATH:
        return "mint"
    if path == LEASE_REDEEM_PATH:
        return "redeem"
    return ""


def lease_transport_allowed(host: str, scheme: str) -> bool:
    """Loopback, or https with certificate verification locked on.

    ``host`` is the socket peer. ``scheme`` is the ASGI scheme. An empty
    peer is refused. ``X-Forwarded-Proto`` is not consulted.
    """
    if peer_is_loopback(host):
        return True
    if not (host or "").strip() or not LEASE_TLS_VERIFY:
        return False
    return (scheme or "").lower() == "https"


def apikey_verify_post(method: str, path: str) -> bool:
    """True only for exact ``POST /api/apikeys/verify``.

    ``path`` is the routed path from ``route_path`` (``get_route_path``), not
    ``request.url.path``. Callers that cannot load that helper must fail closed
    before this returns true. A trailing slash, a different method, or a
    different case stays on the admin gate.
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


def _refuse_lease(request: Request) -> JSONResponse | None:
    """Admit a verified service Bearer. Admin is not a substitute.

    Transport is checked first so a remote plaintext caller learns nothing
    about the bearer. No bearer, or a bearer ``verify_service`` rejects, is
    401 even when ``X-OpenVault-Admin`` matches.
    """
    client = request.client
    host = client.host if client is not None else ""
    scheme = request.scope.get("scheme")
    if not isinstance(scheme, str):
        scheme = ""
    if not lease_transport_allowed(host, scheme):
        log.info("lease_rejected", reason="transport")
        return _bad()
    token = _presented_service_bearer(request)
    if not token:
        log.info("lease_rejected", reason="credential")
        return _missing()
    from openmw.openvault.vault.trust import TrustStore

    seal = getattr(request.app.state, "seal", None)
    service_id = TrustStore(seal=seal).service_id_for_active_bearer(token)
    if not service_id:
        log.info("lease_rejected", reason="service")
        return _missing()
    return None


def _refuse_apikey_verify(request: Request) -> JSONResponse | None:
    decision = decide_apikey_verify(request)
    if decision.admitted:
        return None
    if decision.status_code == 403:
        return _bad()
    return _missing()


def _public_jwks_read(method: str, path: str) -> bool:
    """True only for the published JWKS alt.

    ``path`` is the routed path (see ``route_path``) with no further
    normalisation. HEAD and OPTIONS stay on this side so they match
    ``/.well-known/jwks.json``. Any other method stays on the admin gate and
    answers 401 before a 405.
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
    OPTIONS on exact ``/keys/jwks``), the three POST signing-mint routes,
    exact ``POST /api/apikeys/verify``, and the two POST lease routes.
    Other methods, including GET on the lease paths, POST/PUT/DELETE on
    ``/keys/jwks``, and every other ``/api/apikeys`` path, are guarded the
    same way as every other admin route.
    """
    path = route_path(request)
    # Missing the private router helper must not skip auth and must not 500.
    if path is None:
        return _missing()
    # Same path string as the admin check below. Do not unquote or casefold.
    if _public_jwks_read(request.method, path):
        return None
    if apikey_verify_post(request.method, path):
        return _refuse_apikey_verify(request)
    if lease_post(request.method, path):
        return _refuse_lease(request)

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
    "LEASE_TLS_VERIFY",
    "VERIFY_SERVICES_ENV",
    "ApikeyVerifyDecision",
    "HttpGuardMiddleware",
    "apikey_verify_post",
    "decide_apikey_verify",
    "dev_docs_enabled",
    "lease_post",
    "lease_transport_allowed",
    "path_is_guarded",
    "path_needs_admin",
    "peer_is_loopback",
    "refuse_if_unauthorised",
    "route_path",
    "signing_mint_kind",
    "verify_service_allowlist",
]
