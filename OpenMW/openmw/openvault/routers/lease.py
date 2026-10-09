"""Lease mint and redeem. Exact POST bodies. Kid, ref, and secret stay out of the path.

``POST /api/keys/owner`` is the admin assign path. It is not a service-Bearer
exception. Mint and redeem ignore ``X-OpenVault-Admin`` and any ``service_id``
in the body. The service comes from ``TrustStore.service_id_for_active_bearer``.

Space is the part after ``dms:`` on that service id. The credential is minted
once with ``POST /keys/services`` and reused. The tenant is the admin binding
on the kid. A body ``space`` or ``tenant`` that disagrees is refused. It is
not used as the Space or the tenant.
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
    ERR_SPACE_MISMATCH,
    ERR_TENANT_MISMATCH,
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
    """A query string is a URL channel. Lease routes accept the JSON body only.

    The request is refused and is not redeemed. The process access log can
    still record the request line, including a ref that was placed there.
    """
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


def _optional_text(value: object) -> str:
    """Missing is empty. A non-string is a bad request."""
    if value is None:
        return ""
    return _text(value).strip()


def _claim(body: dict[str, object], key: str) -> str | None:
    """Absent or null means the caller did not send a claim."""
    if key not in body or body[key] is None:
        return None
    return _text(body[key])


def _audit_redeem_refused(request: Request, service_id: str, exc: LeaseError) -> None:
    """Refusal audit is best-effort. It must not hide the lease error."""
    from openmw.openvault.app import _audit_lease_redeem_refused
    from openmw.openvault.vault.trust import space_from_service_id

    try:
        _audit_lease_redeem_refused(
            request,
            reason=exc.code,
            key_id=exc.key_id,
            space=exc.space or space_from_service_id(service_id),
            service_id=service_id,
            owner_tenant=exc.owner_tenant,
        )
    except OSError:
        log.info("lease_refused", reason="lease_redeem_refused_audit")


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
            # space and tenant in the body are claims. The Bearer and the
            # admin binding decide. A disagreeing claim is refused.
            minted = leases.mint(
                vault,
                service_id=service_id,
                kid=_text(body.get("kid")),
                space=_claim(body, "space"),
                tenant=_claim(body, "tenant"),
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
            body_space = _claim(body, "space")
            body_tenant = _claim(body, "tenant")
        except LeaseError as exc:
            return _from_lease(exc)

        def _audit(
            key_id: str,
            space: str,
            owner: str,
            expires_at: int,
            owner_tenant: str,
        ) -> None:
            from openmw.openvault.app import _audit_lease_redeem

            _audit_lease_redeem(
                request,
                key_id=key_id,
                space=space,
                service_id=owner,
                expires_at=expires_at,
                owner_tenant=owner_tenant,
            )

        try:
            redeemed = leases.redeem(
                vault,
                service_id=service_id,
                ref=ref,
                audit=_audit,
                body_space=body_space,
                body_tenant=body_tenant,
            )
        except LeaseError as exc:
            _audit_redeem_refused(request, service_id, exc)
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
        from openmw.openvault.vault.trust import (
            TrustStore,
            service_id_allowed,
            space_from_service_id,
        )

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
            space = _optional_text(body.get("space"))
            tenant = _optional_text(body.get("tenant"))
        except LeaseError as exc:
            return _from_lease(exc)
        if not kid:
            return _error(400, ERR_BAD)
        if service_id:
            if not service_id_allowed(service_id):
                return _error(400, "service_id_invalid")
            trust = TrustStore(seal=getattr(request.app.state, "seal", None))
            if not trust.service_is_registered(service_id):
                return _error(400, "service_not_registered")
            bound_space = space_from_service_id(service_id)
            if bound_space:
                if space and space != bound_space:
                    return _error(403, ERR_SPACE_MISMATCH)
                space = bound_space
            existing = vault.get(kid)
            if existing is None:
                return _error(404, "key_not_found")
            account_id = (existing.account_id or "").strip()
            if tenant and account_id and tenant != account_id:
                return _error(403, ERR_TENANT_MISMATCH)
        if service_id:
            record = vault.set_owner_service(
                kid,
                service_id,
                owner_space=space or None,
                owner_tenant=tenant or None,
            )
        else:
            record = vault.set_owner_service(kid, None)
        if record is None:
            return _error(404, "key_not_found")
        log.info("key_owner_assigned", service_id=service_id or "")
        return JSONResponse(
            content={
                "kid": record.id,
                "owner_service_id": record.owner_service_id or "",
                "owner_space": record.owner_space or "",
                "owner_tenant": record.owner_tenant or "",
                "custody": record.custody,
            },
            headers=_NO_STORE,
        )

    return router


__all__ = ["build_lease_router"]
