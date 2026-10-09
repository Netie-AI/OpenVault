"""Signing-key routes: the trust root, its JWKS, and intermediate issuance.

Mounted by the integrator with ``app.include_router(keys_router)``; this file
declares the routes and owns nothing else.

Path shape is deliberately ``/keys/*`` rather than ``/api/keys/*``. ``/api/keys``
is the provider-credential vault — a different thing with different custody —
and a consumer fetching a public JWKS should not have to reason about which
``keys`` it is talking to. ``GET /keys/jwks`` and ``GET /.well-known/jwks.json``
are the published contracts: Cortex's JWKS refresh fetches a public kid set
without minting. ``/api/keys`` returning ``keys=[]`` is an empty *vault*, not
a missing JWKS.

Reads here are unauthenticated on purpose. A JWKS is public key material; a
verifier that had to authenticate to learn a public key would be a verifier
that stops working the moment credentials expire. Service registration is
loopback plus ``OPENVAULT_SERVICES_ALLOW`` (prove VPC peers). The first
registration for a service_id needs that peer plus the reveal header. A
later registration rotates the token only with the current service Bearer
or ``X-OpenVault-Admin``. Intermediate issue and revoke stay loopback-only.
Issue requires a Bearer service token. Revoke with that Bearer succeeds only
for a kid that same service issued. ``X-OpenVault-Admin`` can revoke any kid.
Loopback alone does not revoke.

The HTTP guard does not require ``X-OpenVault-Admin`` on ``POST /keys/services``,
``POST /keys/intermediate``, or ``POST /keys/intermediate/{kid}/revoke``.
Those three keep the checks in this file. Other ``/keys`` methods stay on
the admin gate. ``GET`` / ``HEAD`` / ``OPTIONS`` ``/keys/jwks`` stay public.
A ``dms:<space>`` registration is stricter than a plain service id: both the
first mint and a later rotation need ``X-OpenVault-Admin`` and a loopback
socket. The services allowlist does not qualify. DMS sends the Space in
lowercase. There is no HTTP route that revokes a service credential.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from openmw.openvault.vault.admin_token import ADMIN_HEADER, admin_token_matches
from openmw.openvault.vault.crypto import VaultSealedError
from openmw.openvault.vault.trust import (
    DEFAULT_INTERMEDIATE_TTL_S,
    DMS_SPACE_INVALID,
    MAX_INTERMEDIATE_TTL_S,
    TrustError,
    TrustStore,
    dms_space_attempt,
    dms_space_canonical,
)

router = APIRouter(tags=["keys"])


def _store(request: Request) -> TrustStore:
    """Use the process Seal. A new Seal() stays sealed after passphrase unseal."""
    return TrustStore(seal=getattr(request.app.state, "seal", None))


def _guards() -> tuple[Any, Any, Any]:
    """Fetch the app-level request guards at call time.

    Imported here rather than at module scope because ``app.py`` imports this
    module while building the application. Reusing its guards instead of
    copying them is the point: a second implementation of "is this loopback"
    is a second thing to get wrong, and the copy would drift silently.
    """
    from openmw.openvault.app import _audit_custody, _require_loopback, _require_reveal_intent

    return _require_loopback, _require_reveal_intent, _audit_custody


def _named(status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": code, "type": code}},
        headers={"Cache-Control": "no-store"},
    )


def _register_dms_space(service_id: str, request: Request) -> JSONResponse | dict[str, Any]:
    """Create or rotate ``dms:<space>``. Admin and loopback, both required.

    Checked before the plain-service peer allowlist. A reveal header and a
    service Bearer, including this Space's own Bearer, do not qualify.
    """
    if not dms_space_canonical(service_id):
        return _named(422, DMS_SPACE_INVALID)
    from openmw.openvault.vault.http_guard import peer_is_loopback

    host = request.client.host if request.client is not None else ""
    if not peer_is_loopback(host):
        return _named(403, "dms_space_loopback_only")
    if not _admin_credential_ok(request):
        return _named(401, "dms_space_admin_required")
    _peer, _intent, audit = _service_registration_guards()
    store = _store(request)
    try:
        token = store.register_service(service_id)
    except TrustError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit("signing_service_registered", request, service_id=service_id)
    return {"service_id": service_id, "token": token}


def _service_registration_guards() -> tuple[Any, Any, Any]:
    """Guards for ``POST /keys/services`` only -- not a widening of loopback."""
    from openmw.openvault.app import (
        _audit_custody,
        _require_reveal_intent,
        _require_signing_service_peer,
    )

    return _require_signing_service_peer, _require_reveal_intent, _audit_custody


def _presented_bearer(request: Request) -> str:
    """Authorization Bearer value, or empty when the header is not a bearer token."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _admin_credential_ok(request: Request) -> bool:
    """True when ``X-OpenVault-Admin`` is present and matches. Absent is false."""
    presented = (request.headers.get(ADMIN_HEADER) or "").strip()
    if not presented:
        return False
    return admin_token_matches(presented)


def _revoke_caller(request: Request, store: TrustStore) -> tuple[str, str]:
    """``("admin", "")``, ``("service", service_id)``, or ``("", "")``.

    Admin wins when the header matches, including if a bearer is also present.
    A bearer counts only when it verifies for exactly one active service.
    """
    if _admin_credential_ok(request):
        return ("admin", "")
    token = _presented_bearer(request)
    if not token:
        return ("", "")
    service_id = store.service_id_for_active_bearer(token)
    if not service_id:
        return ("", "")
    return ("service", service_id)


def _rotation_credential_ok(request: Request, store: TrustStore, service_id: str) -> bool:
    """Current bearer for this service_id, or admin. Reveal is not enough."""
    token = _presented_bearer(request)
    if token and store.verify_service(service_id, token):
        return True
    return _admin_credential_ok(request)


class ServiceRegistration(BaseModel):
    service_id: str = Field(min_length=1, max_length=128)


class IntermediateRequest(BaseModel):
    service_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(default="", max_length=256)
    ttl_s: int = Field(default=DEFAULT_INTERMEDIATE_TTL_S, ge=1, le=MAX_INTERMEDIATE_TTL_S)


_JWKS_HEADERS = {
    "Cache-Control": "public, max-age=60, must-revalidate",
    # Public pin material. Cortex prove is on another host; browser dashboards
    # may also fetch this. Mint routes do not get this header.
    "Access-Control-Allow-Origin": "*",
}


def _jwks_response(request: Request) -> JSONResponse:
    return JSONResponse(content=_store(request).jwks(), headers=_JWKS_HEADERS)


@router.get("/.well-known/jwks.json")
def well_known_jwks(request: Request) -> JSONResponse:
    """RFC 7517 well-known JWKS. Same document as ``GET /keys/jwks``."""
    return _jwks_response(request)


@router.get("/keys/jwks")
def keys_jwks(request: Request) -> JSONResponse:
    """Public JWKS: trust-root pin kid plus any live intermediates.

    Consumers cache this to disk and verify against the cache, so an outage
    here must not stop them verifying. Expired intermediates simply stop being
    listed; their ``exp`` already told the consumer when to stop trusting them.
    The root kid is always present (DR-0014) so Cortex can bind without mint.
    """
    return _jwks_response(request)


@router.get("/keys/root")
def keys_root(request: Request) -> JSONResponse:
    """The trust root's public half, for pinning at install time.

    Served separately from the JWKS so that pinning is a deliberate act, and so
    that nothing signed directly by the root is ever accepted as an ordinary
    signing key. Same CORS as JWKS: Cortex prove is on another origin.
    """
    return JSONResponse(content=_store(request).root_document(), headers=_JWKS_HEADERS)


@router.post("/keys/services", response_model=None)
def register_service(body: ServiceRegistration, request: Request) -> dict[str, str] | JSONResponse:
    """Register a service and return its bearer token — once.

    This is the first authenticator in this application; everything else is
    loopback plus intent. It returns a credential, so it carries the same
    intent header the plaintext-secret route uses: a page the user happens to
    have open cannot mint a service identity with a drive-by POST.

    Peer check is loopback plus configured prove CIDRs, not world-open mint.
    Only the token's SHA-256 is kept. The first registration returns the token.
    Re-registering the same service_id rotates it only when the caller presents
    that service's current Bearer or ``X-OpenVault-Admin``.

    A ``dms:<space>`` id is not that path. Creating it and rotating it both
    require ``X-OpenVault-Admin`` and a loopback socket peer. The services
    allowlist, a reveal header, and any service Bearer do not qualify. The
    Space must already be lowercase (``dms:[a-z0-9][a-z0-9-]{0,62}``). DMS
    normalises before it calls. There is no HTTP route that revokes a service
    credential.
    """
    if dms_space_attempt(body.service_id):
        return _register_dms_space(body.service_id, request)
    require_peer, require_intent, audit = _service_registration_guards()
    require_peer(request, "signing service registration")
    require_intent(request)
    store = _store(request)
    service_id = body.service_id.strip()
    registered = bool(service_id) and store.service_is_registered(service_id)
    if registered and not _rotation_credential_ok(request, store, service_id):
        raise HTTPException(
            status_code=401,
            detail=(
                "re-registering a service requires Authorization: Bearer "
                "<current service token> or X-OpenVault-Admin"
            ),
        )
    try:
        token = store.register_service(body.service_id)
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

    token = _presented_bearer(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail=(
                "intermediate key issue requires an Authorization: Bearer <service token> header"
            ),
        )

    store = _store(request)
    if not store.verify_service(body.service_id, token):
        # Same response for an unknown service and a wrong token: telling them
        # apart turns this into a service-name oracle.
        audit("signing_key_denied", request, service_id=body.service_id)
        raise HTTPException(status_code=403, detail="service is not authorised to sign")

    try:
        issued = store.issue_intermediate(
            body.subject or body.service_id,
            ttl_s=body.ttl_s,
            service_id=body.service_id.strip(),
        )
    except VaultSealedError as exc:
        raise HTTPException(
            status_code=403,
            detail="vault is sealed; POST /api/vault/unseal with the passphrase first",
        ) from exc
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

    A service bearer can drop only a kid that service issued. Admin can drop
    any intermediate. Revocation is only as fast as the consumer's refresh,
    which is why intermediates are short-lived in the first place: this
    narrows the window, it does not close it.
    """
    require_loopback, _intent, audit = _guards()
    require_loopback(request, "intermediate key revoke")
    store = _store(request)
    kind, service_id = _revoke_caller(request, store)
    if not kind:
        raise HTTPException(
            status_code=401,
            detail=(
                "intermediate key revoke requires an Authorization: Bearer "
                "<service token> header or X-OpenVault-Admin"
            ),
        )
    if kind == "service":
        owner = store.intermediate_owner(kid)
        if owner is None:
            raise HTTPException(status_code=404, detail="intermediate key not found")
        if owner != service_id:
            audit("signing_key_revoke_denied", request, service_id=service_id, kid=kid)
            raise HTTPException(
                status_code=403,
                detail="service is not authorised to revoke this key",
            )
    revoked = store.revoke_key(kid)
    if not revoked:
        raise HTTPException(status_code=404, detail="intermediate key not found")
    audit("signing_key_revoked", request, kid=kid)
    return {"kid": kid, "lifecycle": "revoked"}
