"""Stack-detection routes.

Mounted by the integrator with `app.include_router(ship_router)`; this file
declares the routes and owns nothing else. `POST /api/detect` replaces the
inline handler in `app.py` — same path and response shape, plus a real 400 for
input we refuse to guess about.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openmw.openvault.ship.detect import DetectionInputError, detect_project
from openmw.openvault.ship.stacks import STACKS, get_project_type

router = APIRouter(tags=["ship"])


class DetectBody(BaseModel):
    project_path: str


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
            }
            for stack_id, stack in STACKS.items()
        ]
    }
