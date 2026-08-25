"""Stack-detection and OpenVault auto-ship routes.

Mounted by the integrator with `app.include_router(ship_router)`; this file
declares the routes and owns nothing else. `POST /api/detect` replaces the
inline handler in `app.py` — same path and response shape, plus a real 400 for
input we refuse to guess about.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openmw.openvault.ship.cicd import cicd_plan
from openmw.openvault.ship.detect import DetectionInputError, detect_project
from openmw.openvault.ship.hosting import ShipTarget, ready_to_ship, recommend_host
from openmw.openvault.ship.origin import (
    build_origin_plan,
    execute_origin_plan,
    origin_status,
)
from openmw.openvault.ship.pipeline import ship_to_netie
from openmw.openvault.ship.server import build_server_plan, execute_server_plan
from openmw.openvault.ship.service import (
    auto_host,
    connect_service,
    load_session,
    login_service,
    parse_login_kind,
    parse_sku_id,
    quote,
    service_catalog,
)
from openmw.openvault.ship.stripe_billing import (
    apply_checkout_event,
    confirm_checkout,
    create_checkout,
)
from openmw.openvault.ship.stacks import STACKS, get_project_type

router = APIRouter(tags=["ship"])


class DetectBody(BaseModel):
    project_path: str


class ReadyBody(BaseModel):
    project_path: str
    hostname: str = ""
    vps_host: str = ""
    target: ShipTarget | None = None


class AutoShipBody(BaseModel):
    project_path: str
    hostname: str = ""
    vps_host: str = ""
    owner: str = ""
    repo: str = ""
    simulate: bool = True
    write_workflow: bool = False
    session_id: str = ""
    target: ShipTarget = "openvault_hosted"


class ServerShipBody(BaseModel):
    project_path: str
    hostname: str = ""
    vps_host: str = ""
    simulate: bool = True
    target: ShipTarget = "openvault_hosted"


class CicdPlanBody(BaseModel):
    project_path: str
    hostname: str = ""
    vps_host: str = ""
    provider: str = "vps"
    write: bool = False


class ServiceLoginBody(BaseModel):
    email: str
    display_name: str = ""
    login_kind: str = "openvault"
    account_id: str = ""
    sku_id: str | None = None


class ServiceConnectBody(BaseModel):
    session_id: str
    login_kind: str
    vps_host: str = ""
    hostname: str = ""
    aws_region: str = ""
    aws_account_hint: str = ""
    secret: str = ""


class ServiceAutoHostBody(BaseModel):
    session_id: str
    project_path: str = ""
    hostname: str = ""
    simulate: bool = True


class ServiceQuoteBody(BaseModel):
    login_kind: str = "openvault"
    project_path: str = ""
    sku_id: str | None = None


class ServiceCheckoutBody(BaseModel):
    session_id: str
    success_url: str = "http://127.0.0.1:5000/#service"
    cancel_url: str = "http://127.0.0.1:5000/#service"


class ServiceCheckoutConfirmBody(BaseModel):
    session_id: str
    checkout_id: str = ""


class NetieShipBody(BaseModel):
    email: str
    project_path: str
    display_name: str = ""
    sku_id: str = "ov_hosted"
    simulate: bool = True


@router.post("/api/detect")
def api_detect(body: DetectBody) -> dict[str, Any]:
    """Detect the stack at an ABSOLUTE local path.

    An empty or relative path is a 400: resolving it would silently describe the
    server's own working directory instead of the caller's project.
    """
    try:
        return detect_project(body.project_path).to_dict()
    except DetectionInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/ship/stacks")
def api_stacks() -> dict[str, Any]:
    """The stack catalog behind detection — lets the UI offer a manual override
    using the same ids, ports and commands the detector emits."""
    return {
        "stacks": [
            {
                "id": stack_id,
                "name": stack.name,
                "language": stack.language,
                "category": stack.category,
                "project_type": get_project_type(stack_id),
                "default_port": stack.default_port,
                "output_directory": stack.output_directory,
                "build_command": stack.default_build_command,
                "start_command": stack.default_start_command,
                "host_kind": stack.host_kind,
                "origin_http": stack.origin_http,
            }
            for stack_id, stack in STACKS.items()
        ]
    }


@router.get("/api/ship/origin/status")
def api_origin_status() -> dict[str, Any]:
    return origin_status()


@router.post("/api/ship/ready")
def api_ship_ready(body: ReadyBody) -> dict[str, Any]:
    """Ready-to-ship gates: detect type, commands, domain, Caddy/systemd HTTP."""
    try:
        report = ready_to_ship(
            body.project_path,
            hostname=body.hostname,
            vps_host=body.vps_host,
            target=body.target,
        )
    except DetectionInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return report.to_dict()


@router.post("/api/ship/server")
def api_ship_server(body: ServerShipBody) -> dict[str, Any]:
    """Plan (and simulate) Caddy + systemd on Hetzner / VPS / AWS."""
    try:
        stack = detect_project(body.project_path)
    except DetectionInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    plan = build_server_plan(
        project_path=body.project_path,
        hostname=body.hostname,
        vps_host=body.vps_host,
        target=body.target,
        stack=stack,
    )
    executed = execute_server_plan(plan, simulate=body.simulate)
    return executed.to_dict()


@router.post("/api/ship/cicd/plan")
def api_ship_cicd_plan(body: CicdPlanBody) -> dict[str, Any]:
    """GitHub Actions that build and ship to the VPS (not Vercel)."""
    try:
        return cicd_plan(
            body.project_path,
            hostname=body.hostname,
            vps_host=body.vps_host,
            provider=body.provider,
            write=body.write,
        )
    except DetectionInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/ship/auto")
def api_ship_auto(body: AutoShipBody) -> dict[str, Any]:
    """Detect type and ship: Origin git (optional) + OpenVault Caddy/systemd HTTP."""
    hostname = body.hostname
    vps_host = body.vps_host
    target = body.target
    session_payload: dict[str, Any] = {}
    if body.session_id:
        session = load_session(body.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="service session not found")
        hostname = hostname or session.hostname
        vps_host = vps_host or session.vps_host
        if body.target == "openvault_hosted":
            target = "aws" if session.login_kind == "aws" else "openvault_hosted"
        session_payload = session.to_dict()
    try:
        stack = detect_project(body.project_path)
    except DetectionInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    host = recommend_host(
        stack,
        hostname=hostname,
        vps_host=vps_host,
        target=target,
    )
    origin_plan = build_origin_plan(
        project_path=body.project_path,
        hostname=hostname,
        owner=body.owner,
        repo=body.repo,
        stack=stack,
    )
    origin_executed = execute_origin_plan(origin_plan, simulate=body.simulate)
    server_plan = build_server_plan(
        project_path=body.project_path,
        hostname=hostname,
        vps_host=vps_host,
        target=target,
        stack=stack,
    )
    server_executed = execute_server_plan(server_plan, simulate=body.simulate)
    cicd = cicd_plan(
        body.project_path,
        hostname=hostname,
        vps_host=vps_host,
        provider=server_executed.provider,
        write=body.write_workflow,
    )
    ready = ready_to_ship(
        body.project_path,
        hostname=hostname,
        vps_host=vps_host,
        target=target,
    )
    billed = quote(login_kind="openvault", project_path=body.project_path)
    if session_payload:
        billed = quote(
            login_kind=parse_login_kind(str(session_payload.get("login_kind") or "openvault")),
            project_path=body.project_path,
        )
    return {
        "stack": stack.to_dict(),
        "host": host.to_dict(),
        "origin": origin_executed.to_dict(),
        "server": server_executed.to_dict(),
        "cicd": cicd,
        "ready": ready.to_dict(),
        "service": session_payload,
        "quote": billed,
        "laptop": False,
    }


@router.get("/api/service/catalog")
def api_service_catalog() -> dict[str, Any]:
    return service_catalog()


@router.post("/api/service/login")
def api_service_login(body: ServiceLoginBody) -> dict[str, Any]:
    try:
        session = login_service(
            email=body.email,
            display_name=body.display_name,
            login_kind=parse_login_kind(body.login_kind),
            account_id=body.account_id,
            sku_id=parse_sku_id(body.sku_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return session.to_dict()


@router.get("/api/service/session/{session_id}")
def api_service_session(session_id: str) -> dict[str, Any]:
    session = load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="service session not found")
    return session.to_dict()


@router.post("/api/service/connect")
def api_service_connect(body: ServiceConnectBody) -> dict[str, Any]:
    try:
        kind = parse_login_kind(body.login_kind)
        if kind == "openvault":
            raise ValueError("login_kind must be aws, vps, or own_server")
        session = connect_service(
            body.session_id,
            login_kind=kind,
            vps_host=body.vps_host,
            hostname=body.hostname,
            aws_region=body.aws_region,
            aws_account_hint=body.aws_account_hint,
            secret=body.secret,
        )
    except ValueError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return session.to_dict()


@router.post("/api/service/quote")
def api_service_quote(body: ServiceQuoteBody) -> dict[str, Any]:
    try:
        return quote(
            login_kind=parse_login_kind(body.login_kind),
            project_path=body.project_path,
            sku_id=parse_sku_id(body.sku_id),
        )
    except (ValueError, DetectionInputError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/service/auto-host")
def api_service_auto_host(body: ServiceAutoHostBody) -> dict[str, Any]:
    try:
        if body.project_path:
            detect_project(body.project_path)
        return auto_host(
            body.session_id,
            project_path=body.project_path,
            hostname=body.hostname,
            simulate=body.simulate,
        )
    except DetectionInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/api/service/checkout")
def api_service_checkout(body: ServiceCheckoutBody) -> dict[str, Any]:
    try:
        return create_checkout(
            body.session_id,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
        )
    except ValueError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/api/service/checkout/confirm")
def api_service_checkout_confirm(body: ServiceCheckoutConfirmBody) -> dict[str, Any]:
    try:
        return confirm_checkout(body.session_id, checkout_id=body.checkout_id)
    except ValueError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/api/service/stripe/webhook")
def api_service_stripe_webhook(event: dict[str, Any]) -> dict[str, Any]:
    try:
        return apply_checkout_event(event)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/service/ship-netie")
def api_service_ship_netie(body: NetieShipBody) -> dict[str, Any]:
    """Login + Stripe SKU + Caddy ship onto *.netie.ai. Does not touch AirGPT or DMS."""
    try:
        return ship_to_netie(
            email=body.email,
            project_path=body.project_path,
            display_name=body.display_name,
            sku_id=body.sku_id,
            simulate=body.simulate,
        )
    except DetectionInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
