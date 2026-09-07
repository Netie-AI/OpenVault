"""SYSTEM control plane HTTP surface -- entitlements, routing, unlock, metering, seats.

Mounted by ``create_app`` via ``build_system_router(...)``. Paths are
``/api/system/*`` so they never collide with hardware ``/api/control/*``.
Every route is loopback-only: this is not the public rate page, and it is not
a public :5000 bind.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openmw.openvault.vault.accounts import AccountStore
from openmw.openvault.vault.system_plane import (
    EntitlementStore,
    SystemPlaneError,
    bind_policy,
    catalog_payload,
    metering_overlay,
    route_decision,
)
from openmw.openvault.vault.usage_store import UsageStore

GuardHook = Callable[[Request, str], None]
AuditHook = Callable[[Request, str], None]


class UnlockBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=64)
    plan_id: str = Field(min_length=1, max_length=32)
    seats: int | None = Field(default=None, ge=1, le=10000)


class LockBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=64)


class SeatsBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=64)
    seats: int = Field(ge=1, le=10000)


def build_system_router(
    accounts: AccountStore,
    entitlements: EntitlementStore,
    usage: UsageStore,
    *,
    guard: GuardHook | None = None,
    audit: AuditHook | None = None,
) -> APIRouter:
    router = APIRouter(tags=["system"])

    def _guard(request: Request, action: str) -> None:
        if guard is not None:
            guard(request, action)

    def _audit(request: Request, event: str) -> None:
        if audit is not None:
            audit(request, event)

    @router.get("/api/system/catalog")
    def system_catalog(request: Request) -> dict[str, Any]:
        _guard(request, "read system catalog")
        return catalog_payload()

    @router.get("/api/system/bind")
    def system_bind(request: Request) -> dict[str, Any]:
        _guard(request, "read system bind policy")
        return {"ok": True, **bind_policy()}

    @router.get("/api/system/entitlements/{account_id}")
    def system_entitlement(account_id: str, request: Request) -> dict[str, Any]:
        _guard(request, "read entitlement")
        if accounts.get(account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
        return {"ok": True, "entitlement": entitlements.get(account_id).to_dict()}

    @router.get("/api/system/route")
    def system_route(request: Request, account_id: str) -> dict[str, Any]:
        _guard(request, "read system route")
        if accounts.get(account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
        return {"ok": True, **route_decision(entitlements.get(account_id))}

    @router.get("/api/system/metering")
    def system_metering(request: Request, account_id: str) -> dict[str, Any]:
        _guard(request, "read system metering")
        if accounts.get(account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
        return metering_overlay(entitlements.get(account_id), usage.summary())

    @router.post("/api/system/unlock")
    def system_unlock(body: UnlockBody, request: Request) -> dict[str, Any]:
        _guard(request, "unlock plan")
        try:
            record = entitlements.unlock(
                body.account_id, body.plan_id, seats=body.seats, accounts=accounts
            )
        except SystemPlaneError as exc:
            detail = str(exc)
            status = 404 if detail == "account not found" else 400
            raise HTTPException(status_code=status, detail=detail) from exc
        _audit(request, "system_plan_unlocked")
        return {"ok": True, "entitlement": record.to_dict()}

    @router.post("/api/system/lock")
    def system_lock(body: LockBody, request: Request) -> dict[str, Any]:
        _guard(request, "lock plan")
        try:
            record = entitlements.lock(body.account_id, accounts=accounts)
        except SystemPlaneError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _audit(request, "system_plan_locked")
        return {"ok": True, "entitlement": record.to_dict()}

    @router.post("/api/system/seats")
    def system_seats(body: SeatsBody, request: Request) -> dict[str, Any]:
        _guard(request, "set seats")
        try:
            record = entitlements.set_seats(body.account_id, body.seats, accounts=accounts)
        except SystemPlaneError as exc:
            detail = str(exc)
            status = 404 if detail == "account not found" else 400
            raise HTTPException(status_code=status, detail=detail) from exc
        _audit(request, "system_seats_set")
        return {"ok": True, "entitlement": record.to_dict()}

    return router
