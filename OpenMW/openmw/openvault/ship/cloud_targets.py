"""Ship-target catalog, blueprints, and the monthly bill cap."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from openmw.openvault.paths import ensure_home
from openmw.openvault.ship.detect import detect_project
from openmw.openvault.ship.hosting import ShipTarget, recommend_host
from openmw.openvault.ship.origin import origin_status

ShipTargetName = ShipTarget


@dataclass
class BillBudget:
    monthly_cap_usd: float = 25.0
    spent_usd_estimate: float = 0.0
    soft_warn_pct: float = 80.0
    hard_stop: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _budget_path() -> Path:
    return ensure_home() / "ship_budget.json"


def load_bill_budget() -> BillBudget:
    path = _budget_path()
    if not path.is_file():
        return BillBudget()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return BillBudget()
    return BillBudget(
        monthly_cap_usd=float(raw.get("monthly_cap_usd", 25.0)),
        spent_usd_estimate=float(raw.get("spent_usd_estimate", 0.0)),
        soft_warn_pct=float(raw.get("soft_warn_pct", 80.0)),
        hard_stop=bool(raw.get("hard_stop", True)),
    )


def save_bill_budget(budget: BillBudget) -> BillBudget:
    _budget_path().write_text(json.dumps(budget.to_dict(), indent=2), encoding="utf-8")
    return budget


def list_targets() -> dict[str, Any]:
    origin = origin_status()
    return {
        "targets": [
            {
                "id": "cursor_origin",
                "name": "Cursor Origin",
                "git_host": True,
                "http": "vercel_app_for_next_static_hono",
                "http_auto_update": True,
                "load_balancer": "vercel_edge or Caddy on VM",
                "ready": origin.get("ready"),
                "detail": origin.get("notes"),
            },
            {
                "id": "openship_cloud",
                "name": "OpenShip cloud",
                "git_host": False,
                "http": "openship adapter",
                "http_auto_update": True,
                "load_balancer": "openship",
                "ready": True,
            },
            {
                "id": "vps_ssh",
                "name": "Existing VM / VPS",
                "git_host": False,
                "http": "systemd or docker compose",
                "http_auto_update": True,
                "load_balancer": "caddy_or_nginx",
                "ready": True,
            },
            {
                "id": "aws_guide",
                "name": "AWS / Render guide",
                "git_host": False,
                "http": "guide only",
                "http_auto_update": False,
                "load_balancer": "guide",
                "ready": True,
            },
            {
                "id": "local_demo",
                "name": "Local demo",
                "git_host": False,
                "http": "loopback",
                "http_auto_update": False,
                "load_balancer": "none",
                "ready": True,
            },
        ]
    }


def build_ship_blueprint(
    *,
    target: Literal[
        "cursor_origin", "openship_cloud", "vps_ssh", "aws_guide", "local_demo"
    ] = "cursor_origin",
    project_path: str = "",
    hostname: str = "",
    github_url: str = "",
    vps_host: str = "",
    cloud_tier: str = "low",
    monthly_cap_usd: float | None = None,
) -> dict[str, Any]:
    stack = detect_project(project_path) if project_path else None
    host = (
        recommend_host(stack, hostname=hostname, vps_host=vps_host, target=target)
        if stack is not None
        else None
    )
    budget = load_bill_budget()
    cap = monthly_cap_usd if monthly_cap_usd is not None else budget.monthly_cap_usd
    return {
        "target": target,
        "hostname": hostname,
        "github_url": github_url,
        "vps_host": vps_host,
        "cloud_tier": cloud_tier,
        "monthly_cap_usd": cap,
        "stack": stack.to_dict() if stack is not None else {},
        "host": host.to_dict() if host is not None else {},
        "origin": origin_status() if target == "cursor_origin" else {},
        "steps": _blueprint_steps(target, hostname=hostname, vps_host=vps_host),
    }


def _blueprint_steps(target: str, *, hostname: str, vps_host: str) -> list[dict[str, str]]:
    if target == "cursor_origin":
        return [
            {"id": "origin_repo", "title": "Create/push Origin repo"},
            {"id": "vercel_or_vm", "title": "Vercel App (web) or VM (process)"},
            {"id": "dns", "title": f"Point {hostname or '<host>'} at the load balancer"},
        ]
    if target == "vps_ssh":
        return [
            {"id": "ssh", "title": f"SSH {vps_host or '<vps>'}"},
            {"id": "pull", "title": "git pull + restart"},
            {"id": "lb", "title": "Caddy/nginx TLS + HTTP"},
        ]
    if target == "aws_guide":
        return [{"id": "guide", "title": "Follow the AWS/Render plan (no auto-apply)"}]
    if target == "openship_cloud":
        return [{"id": "openship", "title": "OpenShip apps install/update"}]
    return [{"id": "local", "title": "Serve on loopback for demo"}]
