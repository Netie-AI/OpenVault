"""Stack-detection and Origin auto-ship routes.

Mounted by the integrator with `app.include_router(ship_router)`; this file
declares the routes and owns nothing else. `POST /api/detect` replaces the
inline handler in `app.py` — same path and response shape, plus a real 400 for
input we refuse to guess about.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openmw.openvault.ship.detect import DetectionInputError, detect_project
from openmw.openvault.ship.hosting import ready_to_ship, recommend_host
from openmw.openvault.ship.origin import (
    build_origin_plan,
    execute_origin_plan,
    origin_status,
)
from openmw.openvault.ship.stacks import STACKS, get_project_type

router = APIRouter(tags=["ship"])

ShipTarget = Literal[
    "cursor_origin",
    "openship_cloud",
    "vps_ssh",
    "aws_guide",
    "local_demo",
]


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
    owner: str = ""
    repo: str = ""
    simulate: bool = True
    target: ShipTarget = "cursor_origin"


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
    """Ready-to-ship gates: detect type, commands, domain, Origin, HTTP runtime."""
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


@router.post("/api/ship/auto")
def api_ship_auto(body: AutoShipBody) -> dict[str, Any]:
    """Detect type and ship: Origin git + Vercel HTTP, or Origin git + VM."""
    try:
        stack = detect_project(body.project_path)
    except DetectionInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    host = recommend_host(stack, hostname=body.hostname, target=body.target)
    plan = build_origin_plan(
        project_path=body.project_path,
        hostname=body.hostname,
        owner=body.owner,
        repo=body.repo,
        stack=stack,
    )
    executed = execute_origin_plan(plan, simulate=body.simulate)
    ready = ready_to_ship(
        body.project_path,
        hostname=body.hostname,
        target=body.target,
    )
    return {
        "stack": stack.to_dict(),
        "host": host.to_dict(),
        "origin": executed.to_dict(),
        "ready": ready.to_dict(),
    }
