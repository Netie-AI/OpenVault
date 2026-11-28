"""Manager and operator routes for the seat plane. Flag off is 404.

Loopback without a manager token is the operator. A manager token sees only
its tenant. Question text is manager-only. The token is returned once and is
not written to the audit row.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from openmw.openvault.vault.accounts import AccountStore
from openmw.openvault.vault.seat_plane import (
    MANAGER_HEADER,
    Actor,
    SeatError,
    SeatStore,
    seat_plane_enabled,
)
from openmw.openvault.vault.usage_store import UsageStore

GuardHook = Callable[[Request, str], None]


class TenantBody(BaseModel):
    account_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    markup: float | None = None


class SeatBody(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)


class BindBody(BaseModel):
    seat_id: str = Field(min_length=1, max_length=64)
    api_key_id: str = Field(min_length=1, max_length=64)


class ManagerBody(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)


class CapsBody(BaseModel):
    daily_usd: float | None = None
    monthly_usd: float | None = None
    allowance_usd: float | None = None
    ceiling_usd: float | None = None


class AllowlistBody(BaseModel):
    models: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


class MarkupBody(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    markup: float


class CaptureBody(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    enabled: bool
    retention_days: int = 30


def build_seat_router(
    accounts: AccountStore,
    usage: UsageStore,
    *,
    require_loopback: GuardHook,
) -> APIRouter:
    router = APIRouter(tags=["seat-plane"])

    def _store() -> SeatStore:
        return SeatStore(usage.db_path)

    def _actor(request: Request) -> Actor:
        if not seat_plane_enabled():
            raise HTTPException(status_code=404, detail="not found")
        presented = (request.headers.get(MANAGER_HEADER) or "").strip()
        if presented:
            manager = _store().authenticate(presented)
            if manager is None:
                raise HTTPException(status_code=401, detail="unauthorized")
            return Actor("manager", manager.tenant_id, manager.manager_id)
        require_loopback(request, "seat plane")
        return Actor("operator", None, "operator")

    def _call(fn: Any) -> Any:
        try:
            return fn()
        except SeatError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    @router.post("/api/system/tenant/tenants")
    def create_tenant(body: TenantBody, request: Request) -> dict[str, Any]:
        actor = _actor(request)
        if accounts.get(body.account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
        tenant = _call(
            lambda: _store().create_tenant(actor, body.account_id, body.label, body.markup)
        )
        return {"ok": True, "tenant": tenant}

    @router.post("/api/system/tenant/seats")
    def create_seat(body: SeatBody, request: Request) -> dict[str, Any]:
        actor = _actor(request)
        seat = _call(lambda: _store().create_seat(actor, body.tenant_id, body.label))
        return {"ok": True, "seat": seat}

    @router.post("/api/system/tenant/bind")
    def bind_key(body: BindBody, request: Request) -> dict[str, Any]:
        actor = _actor(request)
        return _call(lambda: _store().bind_key(actor, body.seat_id, body.api_key_id))

    @router.post("/api/system/tenant/managers")
    def issue_manager(body: ManagerBody, request: Request) -> dict[str, Any]:
        actor = _actor(request)
        return _call(lambda: _store().issue_manager(actor, body.tenant_id, body.label))

    @router.post("/api/system/tenant/seats/{seat_id}/caps")
    def set_caps(seat_id: str, body: CapsBody, request: Request) -> dict[str, Any]:
        actor = _actor(request)
        updates = {key: getattr(body, key) for key in body.model_fields_set}
        return _call(lambda: _store().set_caps(actor, seat_id, updates))

    @router.put("/api/system/tenant/seats/{seat_id}/allowlist")
    def set_allowlist(seat_id: str, body: AllowlistBody, request: Request) -> dict[str, Any]:
        actor = _actor(request)
        return _call(lambda: _store().set_allowlist(actor, seat_id, body.models, body.roles))

    @router.post("/api/system/tenant/markup")
    def set_markup(body: MarkupBody, request: Request) -> dict[str, Any]:
        actor = _actor(request)
        return _call(lambda: _store().set_markup(actor, body.tenant_id, body.markup))

    @router.post("/api/system/tenant/capture")
    def set_capture(body: CaptureBody, request: Request) -> dict[str, Any]:
        actor = _actor(request)
        return _call(
            lambda: _store().set_capture(actor, body.tenant_id, body.enabled, body.retention_days)
        )

    @router.get("/api/system/tenant/audit")
    def list_audit(request: Request, tenant_id: str = "") -> dict[str, Any]:
        actor = _actor(request)
        return _call(lambda: _store().list_audit(actor, tenant_id))

    @router.get("/api/system/tenant/questions")
    def list_questions(request: Request, tenant_id: str = "") -> dict[str, Any]:
        actor = _actor(request)
        return _call(lambda: _store().list_questions(actor, tenant_id))

    @router.get("/api/system/tenant/billing", response_model=None)
    def billing(
        request: Request, tenant_id: str = "", month: str = "", format: str = "json"
    ) -> Response | dict[str, Any]:
        actor = _actor(request)
        if format not in {"json", "csv"}:
            raise HTTPException(status_code=400, detail="format must be json or csv")
        report = _call(lambda: _store().billing(actor, tenant_id, month))
        if format == "json":
            return report
        return Response(content=_billing_csv(report), media_type="text/csv")

    @router.get("/api/system/tenant/usage")
    def tenant_usage(request: Request) -> dict[str, Any]:
        actor = _actor(request)
        query = dict(request.query_params)
        return _call(lambda: _store().usage_page(actor, query))

    @router.get("/api/system/tenant/policy")
    def policy_status(request: Request) -> dict[str, Any]:
        _actor(request)
        return _store().policy_status()

    return router


def _billing_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "tenant_id",
            "seat_id",
            "label",
            "month",
            "metered_usd",
            "allowance_usd",
            "markup",
            "overage_usd",
            "ceiling_usd",
            "requests",
            "total_tokens",
            "unpriced_requests",
        ]
    )
    for seat in report["seats"]:
        writer.writerow(
            [
                report["tenant_id"],
                seat["seat_id"],
                seat["label"],
                report["month"],
                seat["metered_usd"],
                seat["allowance_usd"],
                seat["markup"],
                seat["overage_usd"],
                "" if seat["ceiling_usd"] is None else seat["ceiling_usd"],
                seat["requests"],
                seat["total_tokens"],
                seat["unpriced_requests"],
            ]
        )
    return buf.getvalue()
