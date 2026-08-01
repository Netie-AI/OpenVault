"""SSE producer for Ship engine deployments — matches apps/web/src/lib/sse/frames.ts."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any

from openmw.openvault.ship.engine import ShipDeployment, load_deployment

# Canonical rail steps (apps/web/src/lib/sse/steps.ts)
STEP_INDEX: dict[str, int] = {
    "prepare": 0,
    "clone": 1,
    "install": 2,
    "build": 3,
    "deploy": 4,
}
STEP_PROGRESS: dict[str, int] = {
    "prepare": 3,
    "clone": 10,
    "install": 30,
    "build": 55,
    "deploy": 80,
}

_ENGINE_TO_RAIL: dict[str, str] = {
    "source": "prepare",
    "detect": "prepare",
    "cicd": "install",
    "domain": "prepare",
    "preflight": "prepare",
    "build": "build",
    "install": "install",
    "host": "deploy",
    "openship": "deploy",
    "vps": "deploy",
    "aws": "deploy",
    "local": "deploy",
}


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _rail_step(engine_step_id: str, *, github: bool) -> str:
    if engine_step_id == "source" and github:
        return "clone"
    return _ENGINE_TO_RAIL.get(engine_step_id, "prepare")


def _ui_status(status: str) -> str:
    if status == "pass":
        return "completed"
    if status == "fail":
        return "failed"
    if status in ("skipped", "simulated"):
        return "skipped"
    return "running"


def _frame(event_id: int, payload: dict[str, Any]) -> str:
    body = dict(payload)
    body.setdefault("eventId", event_id)
    data = json.dumps(body, separators=(",", ":"))
    return f"id: {event_id}\ndata: {data}\n\n"


def deployment_frames(
    dep: ShipDeployment,
    *,
    last_event_id: int = 0,
) -> list[str]:
    """Build the full SSE replay for a finished (or partial) deployment."""
    frames: list[str] = []
    eid = 0

    def emit(payload: dict[str, Any]) -> None:
        nonlocal eid
        eid += 1
        if eid <= last_event_id:
            return
        frames.append(_frame(eid, payload))

    emit({"type": "connected", "message": f"deployment {dep.deployment_id}"})
    emit({"type": "started", "message": f"target={dep.target}"})

    github = bool(dep.github_url.strip())
    for step in dep.steps:
        rail = _rail_step(step.id, github=github)
        ui_status = _ui_status(step.status)
        progress = STEP_PROGRESS.get(rail, 0)
        if ui_status == "completed":
            progress += 10
        emit(
            {
                "type": "phase",
                "phase": step.title or step.id,
                "step": rail,
                "stepStatus": ui_status if ui_status != "running" else "running",
                "currentStep": STEP_INDEX.get(rail, 0),
                "progress": progress,
            }
        )
        emit(
            {
                "type": "progress",
                "step": rail,
                "stepStatus": ui_status,
                "currentStep": STEP_INDEX.get(rail, 0),
                "progress": progress,
            }
        )
        line = f"[{step.status}] {step.title}: {step.detail or step.id}\n"
        emit(
            {
                "type": "log",
                "step": rail,
                "stepStatus": ui_status,
                "data": _b64(line),
                "level": "error" if step.status == "fail" else "info",
            }
        )

    failed = any(s.status == "fail" for s in dep.steps)
    success = bool(dep.ready) and not failed
    if dep.public_url:
        emit({"type": "log", "data": _b64(f"public_url={dep.public_url}\n"), "level": "info"})
    emit(
        {
            "type": "complete",
            "success": success,
            "message": "ready" if success else "finished with issues",
            "exitCode": 0 if success else 1,
        }
    )
    emit({"type": "end", "exitCode": 0 if success else 1, "success": success})
    return frames


async def stream_deployment(
    deployment_id: str,
    *,
    last_event_id: int = 0,
    pace_s: float = 0.02,
) -> AsyncIterator[bytes]:
    """Async SSE byte stream for ``GET /api/ship/engine/{id}/stream``."""
    dep = load_deployment(deployment_id)
    if dep is None:
        payload = {
            "type": "error",
            "error": "deployment not found",
            "errorCode": "not_found",
            "eventId": 1,
            "success": False,
        }
        yield f"id: 1\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
        return

    for frame in deployment_frames(dep, last_event_id=last_event_id):
        yield frame.encode("utf-8")
        if pace_s > 0:
            await asyncio.sleep(pace_s)
