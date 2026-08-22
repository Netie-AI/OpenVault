"""In-process ship engine — detect type, pick Origin/VM/static, record a deployment."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

from openmw.openvault.paths import ensure_home
from openmw.openvault.ship.cloud_targets import build_ship_blueprint
from openmw.openvault.ship.detect import detect_project
from openmw.openvault.ship.hosting import recommend_host
from openmw.openvault.ship.origin import build_origin_plan, execute_origin_plan, origin_status

log = structlog.get_logger()

EngineTarget = Literal[
    "cursor_origin",
    "openship_cloud",
    "vps_ssh",
    "aws_guide",
    "local_demo",
]


@dataclass
class Deployment:
    deployment_id: str
    target: str
    project_path: str
    hostname: str
    ok: bool
    error: str = ""
    blueprint: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    stack: dict[str, Any] = field(default_factory=dict)
    host: dict[str, Any] = field(default_factory=dict)
    origin: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _deployments_dir() -> Path:
    path = ensure_home() / "engine_deploys"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_deployment(dep: Deployment) -> Path:
    path = _deployments_dir() / f"{dep.deployment_id}.json"
    path.write_text(json.dumps(dep.to_dict(), indent=2), encoding="utf-8")
    return path


def load_deployment(deployment_id: str) -> Deployment | None:
    path = _deployments_dir() / f"{deployment_id}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Deployment(
        deployment_id=raw["deployment_id"],
        target=raw.get("target", "local_demo"),
        project_path=raw.get("project_path", ""),
        hostname=raw.get("hostname", ""),
        ok=bool(raw.get("ok")),
        error=str(raw.get("error") or ""),
        blueprint=dict(raw.get("blueprint") or {}),
        steps=list(raw.get("steps") or []),
        stack=dict(raw.get("stack") or {}),
        host=dict(raw.get("host") or {}),
        origin=dict(raw.get("origin") or {}),
        created_at=float(raw.get("created_at", time.time())),
    )


def run_ship_engine(
    *,
    target: EngineTarget = "local_demo",
    project_path: str = "",
    github_url: str = "",
    hostname: str = "",
    vps_host: str = "",
    cloud_tier: str = "low",
    monthly_cap_usd: float | None = None,
    run_build: bool = False,
    prefer_remote_openship: bool = False,
) -> dict[str, Any]:
    """Plan (and optionally simulate) a type-based ship to Origin / VM / local."""
    del run_build, prefer_remote_openship  # reserved; detect commands are the build plan
    if not project_path and not github_url:
        return {"ok": False, "error": "project_path or github_url required", "deployment": {}}

    work = project_path
    try:
        stack = detect_project(work) if work else None
    except Exception as exc:
        return {"ok": False, "error": str(exc), "deployment": {}}

    if stack is None:
        return {"ok": False, "error": "no local project_path to detect", "deployment": {}}

    host = recommend_host(stack, hostname=hostname, vps_host=vps_host, target=target)
    blueprint = build_ship_blueprint(
        target=target,
        project_path=work,
        hostname=hostname,
        github_url=github_url,
        vps_host=vps_host,
        cloud_tier=cloud_tier,
        monthly_cap_usd=monthly_cap_usd,
    )
    steps: list[dict[str, Any]] = [
        {
            "id": "detect",
            "status": "pass" if stack.primary != "unknown" else "fail",
            "detail": f"{stack.framework or stack.primary} kind={host.host_kind}",
        }
    ]
    origin_payload: dict[str, Any] = origin_status()
    if target == "cursor_origin":
        plan = build_origin_plan(project_path=work, hostname=hostname, stack=stack)
        executed = execute_origin_plan(plan, simulate=True)
        origin_payload = executed.to_dict()
        steps.extend(asdict(s) for s in executed.steps)

    ok = stack.primary != "unknown"
    dep = Deployment(
        deployment_id=uuid.uuid4().hex[:12],
        target=target,
        project_path=stack.project_path,
        hostname=hostname,
        ok=ok,
        error="" if ok else "unknown stack",
        blueprint=blueprint,
        steps=steps,
        stack=stack.to_dict(),
        host=host.to_dict(),
        origin=origin_payload,
    )
    save_deployment(dep)
    log.info("ship_engine", deployment_id=dep.deployment_id, target=target, ok=ok)
    payload = dep.to_dict()
    return {"ok": ok, "error": dep.error or None, "deployment": payload}
