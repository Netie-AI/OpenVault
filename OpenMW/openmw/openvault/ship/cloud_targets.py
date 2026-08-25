"""Ship-target catalog, blueprints, and the monthly bill cap."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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
                "id": "vps_ssh",
                "name": "VPS / any box",
                "git_host": False,
                "http": "caddy_plus_systemd",
                "http_auto_update": True,
                "load_balancer": "caddy",
                "ready": True,
                "detail": "Default. Replaces Vercel. Caddy TLS + systemd + /healthz.",
            },
            {
                "id": "hetzner",
                "name": "Hetzner",
                "git_host": False,
                "http": "caddy_plus_systemd",
                "http_auto_update": True,
                "load_balancer": "caddy",
                "ready": True,
                "detail": "Same OpenVault server plan, Hetzner-labelled SSH host.",
            },
            {
                "id": "aws",
                "name": "AWS EC2 + SSM",
                "git_host": False,
                "http": "caddy_plus_systemd_ssm",
                "http_auto_update": True,
                "load_balancer": "caddy",
                "ready": True,
                "detail": "Same unit file; restart via AWS Systems Manager send-command.",
            },
            {
                "id": "cursor_origin",
                "name": "Cursor Origin (git only)",
                "git_host": True,
                "http": "openvault_caddy_on_vps",
                "http_auto_update": True,
                "load_balancer": "caddy",
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
                "id": "aws_guide",
                "name": "AWS guide (alias of aws)",
                "git_host": False,
                "http": "caddy_plus_systemd_ssm",
                "http_auto_update": True,
                "load_balancer": "caddy",
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
    target: ShipTarget = "vps_ssh",
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
    dns = f"Point {hostname or '<host>'} A record at the VPS"
    if target == "cursor_origin":
        return [
            {"id": "origin_repo", "title": "Create/push Origin repo"},
            {"id": "openvault_http", "title": "Caddy + systemd on the VPS (not Vercel)"},
            {"id": "dns", "title": dns},
        ]
    if target in {"vps_ssh", "hetzner"}:
        return [
            {"id": "ssh", "title": f"SSH {vps_host or '<vps>'}"},
            {"id": "sync", "title": "rsync / git pull"},
            {"id": "service", "title": "systemd enable --now"},
            {"id": "lb", "title": "Caddy TLS + reverse_proxy + /healthz"},
        ]
    if target in {"aws", "aws_guide"}:
        return [
            {"id": "ec2", "title": f"SSH or SSM {vps_host or '<instance>'}"},
            {"id": "service", "title": "systemd unit + AWS SSM restart"},
            {"id": "lb", "title": "Caddy TLS + /healthz"},
        ]
    if target == "openship_cloud":
        return [{"id": "openship", "title": "OpenShip apps install/update"}]
    return [{"id": "local", "title": "Serve on loopback for demo"}]
