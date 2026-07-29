"""Signing-key routes: the trust root, its JWKS, and intermediate issuance.

Mounted by the integrator with ``app.include_router(keys_router)``; this file
declares the routes and owns nothing else.

Path shape is deliberately ``/keys/*`` rather than ``/api/keys/*``. ``/api/keys``
is the provider-credential vault — a different thing with different custody —
and a consumer fetching a public JWKS should not have to reason about which
``keys`` it is talking to. ``GET /keys/jwks`` is also a published contract:
Cortex's manifest verifier fetches exactly that path.

Reads here are unauthenticated on purpose. A JWKS is public key material; a
verifier that had to authenticate to learn a public key would be a verifier
that stops working the moment credentials expire. Everything that *mints* is
loopback-only and, for issuance, bearer-authenticated.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openmw.openvault.vault.trust import (
    DEFAULT_INTERMEDIATE_TTL_S,
    MAX_INTERMEDIATE_TTL_S,
    TrustError,
    TrustStore,
)

router = APIRouter(tags=["keys"])


def _store() -> TrustStore:
    return TrustStore()


def _guards() -> tuple[Any, Any, Any]:
    """Fetch the app-level request guards at call time.

    Imported here rather than at module scope because ``app.py`` imports this
    module while building the application. Reusing its guards instead of
    copying them is the point: a second implementation of "is this loopback"
    is a second thing to get wrong, and the copy would drift silently.
    """
    from openmw.openvault.app import _audit_custody, _require_loopback, _require_reveal_intent

    return _require_loopback, _require_reveal_intent, _audit_custody


class ServiceRegistration(BaseModel):
    service_id: str = Field(min_length=1, max_length=128)


class IntermediateRequest(BaseModel):
    service_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(default="", max_length=256)
    ttl_s: int = Field(default=DEFAULT_INTERMEDIATE_TTL_S, ge=1, le=MAX_INTERMEDIATE_TTL_S)


@router.get("/keys/jwks")
def keys_jwks() -> dict[str, Any]:
    """Public keys for every currently valid intermediate.

    Consumers cache this to disk and verify against the cache, so an outage
    here must not stop them verifying. Expired intermediates simply stop being
    listed; their ``exp`` already told the consumer when to stop trusting them.
    """
    return _store().jwks()


@router.get("/keys/root")
def keys_root() -> dict[str, Any]:
    """The trust root's public half, for pinning at install time.

    Served separately from the JWKS so that pinning is a deliberate act, and so
    that nothing signed directly by the root is ever accepted as an ordinary
    signing key.
    """
    return _store().root_document()


@router.post("/keys/services")
def register_service(body: ServiceRegistration, request: Request) -> dict[str, str]:
    """Register a service and return its bearer token — once.

    This is the first authenticator in this application; everything else is
    loopback plus intent. It returns a credential, so it carries the same
    intent header the plaintext-secret route uses: a page the user happens to
    have open cannot mint a service identity with a drive-by POST.

    Only the token's SHA-256 is kept. Re-registering the same service_id issues
    a new token and invalidates the old one, which is the rotation path.
    """
    require_loopback, require_intent, audit = _guards()
    require_loopback(request, "signing service registration")
    require_intent(request)
    try:
        token = _store().register_service(body.service_id)
    except TrustError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit("signing_service_registered", request, service_id=body.service_id)
    return {"service_id": body.service_id, "token": token}


@router.post("/keys/intermediate")
def issue_intermediate(body: IntermediateRequest, request: Request) -> dict[str, Any]:
    """Issue a short-lived intermediate signing key to an authenticated service.

    The private half is in this response and nowhere else — it is not stored,
    so it cannot be re-served, and it is never written to the audit line.
    """
    require_loopback, _intent, audit = _guards()
    require_loopback(request, "intermediate key issue")

    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail=(
                "intermediate key issue requires an "
                "Authorization: Bearer <service token> header"
            ),
        )

    store = _store()
    if not store.verify_service(body.service_id, token.strip()):
        # Same response for an unknown service and a wrong token: telling them
        # apart turns this into a service-name oracle.
        audit("signing_key_denied", request, service_id=body.service_id)
        raise HTTPException(status_code=403, detail="service is not authorised to sign")

    try:
        issued = store.issue_intermediate(body.subject or body.service_id, ttl_s=body.ttl_s)
    except TrustError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit(
        "signing_key_issued",
        request,
        service_id=body.service_id,
        kid=issued.record.kid,
        expires_at=int(issued.record.not_after or 0),
    )
    return issued.to_payload()


@router.post("/keys/intermediate/{kid}/revoke")
def revoke_intermediate(kid: str, request: Request) -> dict[str, Any]:
    """Drop a key from the JWKS before its own expiry.

    Revocation is only as fast as the consumer's refresh, which is why
    intermediates are short-lived in the first place — this narrows the window,
    it does not close it.
    """
    require_loopback, _intent, audit = _guards()
    require_loopback(request, "intermediate key revoke")
    revoked = _store().revoke_key(kid)
    if not revoked:
        raise HTTPException(status_code=404, detail="intermediate key not found")
    audit("signing_key_revoked", request, kid=kid)
    return {"kid": kid, "lifecycle": "revoked"}
