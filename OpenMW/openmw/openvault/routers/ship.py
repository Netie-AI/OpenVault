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
from openmw.openvault.ship.server import build_server_plan, execute_server_plan
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
    target: ShipTarget = "vps_ssh"


class ServerShipBody(BaseModel):
    project_path: str
    hostname: str = ""
    vps_host: str = ""
    simulate: bool = True
    target: ShipTarget = "vps_ssh"


class CicdPlanBody(BaseModel):
    project_path: str
    hostname: str = ""
    vps_host: str = ""
    provider: str = "vps"
    write: bool = False


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
    try:
        stack = detect_project(body.project_path)
    except DetectionInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    host = recommend_host(
        stack,
        hostname=body.hostname,
        vps_host=body.vps_host,
        target=body.target,
    )
    origin_plan = build_origin_plan(
        project_path=body.project_path,
        hostname=body.hostname,
        owner=body.owner,
        repo=body.repo,
        stack=stack,
    )
    origin_executed = execute_origin_plan(origin_plan, simulate=body.simulate)
    server_plan = build_server_plan(
        project_path=body.project_path,
        hostname=body.hostname,
        vps_host=body.vps_host,
        target=body.target,
        stack=stack,
    )
    server_executed = execute_server_plan(server_plan, simulate=body.simulate)
    cicd = cicd_plan(
        body.project_path,
        hostname=body.hostname,
        vps_host=body.vps_host,
        provider=server_executed.provider,
        write=body.write_workflow,
    )
    ready = ready_to_ship(
        body.project_path,
        hostname=body.hostname,
        vps_host=body.vps_host,
        target=body.target,
    )
    return {
        "stack": stack.to_dict(),
        "host": host.to_dict(),
        "origin": origin_executed.to_dict(),
        "server": server_executed.to_dict(),
        "cicd": cicd,
        "ready": ready.to_dict(),
    }
