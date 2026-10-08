"""Lease mint and redeem. Exact POST bodies. Kid, ref, and secret stay out of the path.

``POST /api/keys/owner`` is the admin assign path. It is not a service-Bearer
exception. Mint and redeem ignore ``X-OpenVault-Admin`` and any ``service_id``
in the body. The service comes from ``TrustStore.service_id_for_active_bearer``.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from openmw.openvault.vault.lease import (
    DEFAULT_LEASE_TTL_S,
    ERR_BAD,
    ERR_REF_IN_URL,
    ERR_REF_INVALID,
    ERR_TRANSPORT,
    LEASE_MINT_PATH,
    LEASE_REDEEM_PATH,
    MAX_LEASE_TTL_S,
    OWNER_ASSIGN_PATH,
    LeaseError,
    LeaseStore,
)
from openmw.openvault.vault.store import KeyVault

log = structlog.get_logger()

_NO_STORE = {"Cache-Control": "no-store"}


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": code, "type": code}},
        headers=_NO_STORE,
    )


def _from_lease(exc: LeaseError) -> JSONResponse:
    return _error(exc.status, exc.code)


async def _object_body(request: Request) -> dict[str, object] | None:
    """JSON object, or None. The raw text is not logged and not copied into errors."""
    try:
        payload = await request.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _bearer_service_id(request: Request) -> str:
    """Service id for this Bearer via ``verify_service``. Empty when it does not match.

    The body is not read. ``X-OpenVault-Admin`` is not a service.
    """
    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    token = token.strip()
    if not token:
        return ""
    from openmw.openvault.vault.trust import TrustStore

    store = TrustStore(seal=getattr(request.app.state, "seal", None))
    return store.service_id_for_active_bearer(token)


def _transport_error(request: Request) -> JSONResponse | None:
    from openmw.openvault.vault.http_guard import lease_transport_allowed

    client = request.client
    host = client.host if client is not None else ""
    scheme = request.scope.get("scheme")
    if not isinstance(scheme, str):
        scheme = ""
    if lease_transport_allowed(host, scheme):
        return None
    log.info("lease_refused", reason=ERR_TRANSPORT)
    return _error(403, ERR_TRANSPORT)


def _query_error(request: Request) -> JSONResponse | None:
    """A query string is a URL channel. Lease routes accept the JSON body only."""
    if not request.url.query:
        return None
    log.info("lease_refused", reason=ERR_REF_IN_URL)
    return _error(400, ERR_REF_IN_URL)


def _ttl(value: object) -> int:
    if value is None:
        return DEFAULT_LEASE_TTL_S
    if isinstance(value, bool) or not isinstance(value, int):
        raise LeaseError(ERR_BAD, 400)
    if value < 1 or value > MAX_LEASE_TTL_S:
        raise LeaseError(ERR_BAD, 400)
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise LeaseError(ERR_BAD, 400)
    return value


def build_lease_router(vault: KeyVault) -> APIRouter:
    """Mint, redeem, and the admin owner-assign route. Bound to ``vault``."""
    router = APIRouter(tags=["leases"])
    leases = LeaseStore(db_path=vault.db_path)

    @router.post(LEASE_MINT_PATH)
    async def mint_lease(request: Request) -> JSONResponse:
        """One kid, one Space, short TTL. Caller identity is the Bearer only."""
        refused = _query_error(request) or _transport_error(request)
        if refused is not None:
            return refused
        service_id = _bearer_service_id(request)
        if not service_id:
            return _error(401, "openvault_unauthenticated")
        body = await _object_body(request)
        if body is None:
            return _error(400, ERR_BAD)
        try:
            # service_id in the body is ignored on purpose.
            minted = leases.mint(
                vault,
                service_id=service_id,
                kid=_text(body.get("kid")),
                space=_text(body.get("space")),
                ttl_s=_ttl(body.get("ttl_s")),
            )
        except LeaseError as exc:
            return _from_lease(exc)
        return JSONResponse(content=minted.to_dict(), headers=_NO_STORE)

    @router.post(LEASE_REDEEM_PATH)
    async def redeem_lease(request: Request) -> JSONResponse:
        """Single use. The ref is a JSON field, never a path segment."""
        refused = _query_error(request) or _transport_error(request)
        if refused is not None:
            return refused
        service_id = _bearer_service_id(request)
        if not service_id:
            return _error(401, "openvault_unauthenticated")
        body = await _object_body(request)
        if body is None:
            return _error(400, ERR_REF_INVALID)
        ref = body.get("ref")
        if not isinstance(ref, str):
            return _error(400, ERR_REF_INVALID)
        try:
            redeemed = leases.redeem(vault, service_id=service_id, ref=ref)
        except LeaseError as exc:
            return _from_lease(exc)
        return JSONResponse(content=redeemed.to_dict(), headers=_NO_STORE)

    @router.post(OWNER_ASSIGN_PATH)
    async def assign_owner(request: Request) -> JSONResponse:
        """Admin assigns kid ownership. A service Bearer does not.

        Existing kids stay unowned (NULL) until this runs. An empty
        ``service_id`` clears the owner. The kid is not put on the path.
        """
        from openmw.openvault.app import _require_loopback, _require_unsealed
        from openmw.openvault.vault.admin_token import ADMIN_HEADER, admin_token_matches
        from openmw.openvault.vault.trust import TrustStore

        presented = (request.headers.get(ADMIN_HEADER) or "").strip()
        if not presented or not admin_token_matches(presented):
            return _error(401, "openvault_unauthenticated")
        _require_loopback(request, "key owner assign")
        _require_unsealed(vault.seal, "key owner assign")
        body = await _object_body(request)
        if body is None:
            return _error(400, ERR_BAD)
        try:
            kid = _text(body.get("kid")).strip()
            service_id = _text(body.get("service_id")).strip()
        except LeaseError as exc:
            return _from_lease(exc)
        if not kid:
            return _error(400, ERR_BAD)
        if service_id:
            trust = TrustStore(seal=getattr(request.app.state, "seal", None))
            if not trust.service_is_registered(service_id):
                return _error(400, "service_not_registered")
        record = vault.set_owner_service(kid, service_id or None)
        if record is None:
            return _error(404, "key_not_found")
        log.info("key_owner_assigned", service_id=service_id or "")
        return JSONResponse(
            content={
                "kid": record.id,
                "owner_service_id": record.owner_service_id or "",
                "custody": record.custody,
            },
            headers=_NO_STORE,
        )

    return router


__all__ = ["build_lease_router"]
