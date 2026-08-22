"""Map a detected stack onto a runtime: Origin git + Vercel HTTP, VM, or static serve.

Cursor Origin is a git forge (https://cursor.com/docs/origin). It does not run
apps. HTTP auto-update for Next.js / Vite / Astro / static / Hono is the Origin
→ Vercel App path (PR preview, merge = production). Process and container apps
still push git to Origin, then run on a VM / compose / local static server with
Caddy/nginx as the load balancer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from openmw.openvault.ship.detect import DetectedStack, detect_project
from openmw.openvault.ship.origin import origin_status
from openmw.openvault.ship.stacks import get_stack

ShipTarget = Literal[
    "cursor_origin",
    "openship_cloud",
    "vps_ssh",
    "aws_guide",
    "local_demo",
]

RuntimeKind = Literal[
    "vercel_app",
    "static_serve",
    "vm_process",
    "docker_compose",
    "local_demo",
]


@dataclass(frozen=True)
class HostPlan:
    git_target: str
    runtime: RuntimeKind
    host_kind: str
    http_auto_update: bool
    load_balancer: str
    needs_vm: bool
    needs_static_serve: bool
    recommended_target: ShipTarget
    detail: str
    domain_records: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReadyReport:
    ready: bool
    stack: dict[str, Any]
    host: dict[str, Any]
    origin: dict[str, Any]
    gates: list[dict[str, Any]]
    blockers: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def host_kind_for(stack: DetectedStack) -> str:
    if stack.host_kind:
        return stack.host_kind
    spec = get_stack(stack.framework) or get_stack(stack.primary)
    if spec is not None:
        return spec.host_kind
    return "unknown"


def recommend_host(
    stack: DetectedStack,
    *,
    hostname: str = "",
    vps_host: str = "",
    target: ShipTarget | None = None,
) -> HostPlan:
    kind = host_kind_for(stack)
    records: list[str] = []
    if hostname and "." in hostname:
        records = [
            f"A {hostname} → <lb-or-vercel>",
            f"CNAME www.{hostname} → {hostname}",
        ]

    if kind in {"static_http", "edge_http"}:
        runtime: RuntimeKind = "vercel_app"
        lb = "vercel_edge"
        needs_vm = False
        needs_static = kind == "static_http"
        recommended: ShipTarget = "cursor_origin"
        detail = (
            "Push git to Cursor Origin; Vercel App serves HTTP and auto-updates "
            "on push/PR (preview) and merge (production)."
        )
        if kind == "static_http":
            detail += " Static files can also be served by Caddy/nginx on a VM if Vercel is off."
    elif kind == "container":
        runtime = "docker_compose"
        lb = "caddy_or_nginx"
        needs_vm = True
        needs_static = False
        recommended = "vps_ssh" if vps_host else "local_demo"
        detail = (
            "Push git to Cursor Origin. Run compose/Dockerfile on a VM; "
            "Caddy/nginx terminates TLS and load-balances HTTP."
        )
    elif kind == "process":
        runtime = "vm_process"
        lb = "caddy_or_nginx"
        needs_vm = True
        needs_static = False
        recommended = "vps_ssh" if vps_host else "local_demo"
        detail = (
            "Push git to Cursor Origin. Run the process (uvicorn/gunicorn/node) "
            "on a VM; HTTP auto-update is git pull + restart behind the load balancer."
        )
    else:
        runtime = "local_demo"
        lb = "none"
        needs_vm = False
        needs_static = False
        recommended = "local_demo"
        detail = "Stack unknown — detect a Next.js/static/Python/Docker tree first."

    chosen: ShipTarget = target or recommended
    if chosen == "local_demo":
        runtime = "local_demo"

    vm_needed = bool(needs_vm and chosen in {"vps_ssh", "openship_cloud", "local_demo"})
    return HostPlan(
        git_target="cursor_origin",
        runtime=runtime,
        host_kind=kind,
        http_auto_update=kind in {"static_http", "edge_http"} or chosen != "aws_guide",
        load_balancer=lb,
        needs_vm=vm_needed,
        needs_static_serve=needs_static,
        recommended_target=chosen if target else recommended,
        detail=detail,
        domain_records=records,
    )


def ready_to_ship(
    project_path: str,
    *,
    hostname: str = "",
    vps_host: str = "",
    target: ShipTarget | None = None,
) -> ReadyReport:
    """Gates that must be green before auto-ship (detect, commands, domain, Origin)."""
    stack = detect_project(project_path)
    host = recommend_host(stack, hostname=hostname, vps_host=vps_host, target=target)
    origin = origin_status()
    gates: list[dict[str, Any]] = []
    blockers: list[str] = []

    detect_ok = stack.primary != "unknown" and stack.confidence >= 0.5
    gates.append(
        {
            "id": "detect",
            "title": "Auto-detect project type",
            "status": "pass" if detect_ok else "fail",
            "detail": f"{stack.framework or stack.primary} conf={stack.confidence:.2f}",
        }
    )
    if not detect_ok:
        blockers.append("detect")

    cmds_ok = True
    if stack.framework == "static" or stack.category == "static":
        cmd_detail = "static site — no compile step"
    elif stack.suggested_build or stack.start_command or stack.install_command:
        cmd_detail = " → ".join(
            c for c in (stack.install_command, stack.build_command, stack.start_command) if c
        )
        if stack.warnings:
            cmds_ok = False
            cmd_detail += f"; warnings={list(stack.warnings)}"
    else:
        cmds_ok = False
        cmd_detail = "no install/build/start commands"
    gates.append(
        {
            "id": "commands",
            "title": "Install / build / start by type",
            "status": "pass" if cmds_ok else "fail",
            "detail": cmd_detail,
        }
    )
    if not cmds_ok:
        blockers.append("commands")

    domain_ok = bool(hostname) and "." in hostname
    gates.append(
        {
            "id": "domain",
            "title": "Public hostname + load balancer records",
            "status": "pass" if domain_ok else "fail",
            "detail": (
                "; ".join(host.domain_records)
                if domain_ok
                else "hostname like app.example.com required"
            ),
        }
    )
    if not domain_ok:
        blockers.append("domain")

    origin_ok = bool(origin.get("ready"))
    gates.append(
        {
            "id": "origin",
            "title": "Cursor Origin git host",
            "status": "pass" if origin_ok else "pending",
            "detail": origin.get("detail") or "ORIGIN_MODE=simulate or origin CLI",
        }
    )
    if host.recommended_target == "cursor_origin" and not origin_ok:
        blockers.append("origin")

    runtime_ok = True
    runtime_detail = f"{host.runtime} lb={host.load_balancer} vm={host.needs_vm}"
    if host.needs_vm and not vps_host and host.recommended_target == "vps_ssh":
        runtime_ok = False
        runtime_detail = "needs a VM host (vps_host) or switch to local_demo"
    gates.append(
        {
            "id": "runtime",
            "title": "HTTP runtime (Vercel / static serve / VM)",
            "status": "pass" if runtime_ok else "fail",
            "detail": runtime_detail,
        }
    )
    if not runtime_ok:
        blockers.append("runtime")

    gates.append(
        {
            "id": "http_auto_update",
            "title": "HTTP auto-update",
            "status": "pass" if host.http_auto_update else "pending",
            "detail": (
                "Origin push → Vercel preview/production"
                if host.runtime == "vercel_app"
                else "git pull + process restart behind load balancer"
            ),
        }
    )

    # Origin simulate still counts as ready for the git step when ORIGIN_MODE=simulate.
    if "origin" in blockers and origin.get("mode") == "simulate":
        blockers = [b for b in blockers if b != "origin"]
        for gate in gates:
            if gate["id"] == "origin":
                gate["status"] = "pass"
                gate["detail"] = "ORIGIN_MODE=simulate — plan only, no live push"

    ready = not blockers
    return ReadyReport(
        ready=ready,
        stack=stack.to_dict(),
        host=host.to_dict(),
        origin=origin,
        gates=gates,
        blockers=blockers,
    )
