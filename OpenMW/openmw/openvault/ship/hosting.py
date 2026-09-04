"""Map a detected stack onto OpenVault HTTP: Caddy + systemd on VPS/Hetzner/AWS.

Cursor Origin is git-only (https://cursor.com/docs/origin). OpenVault replaces
Vercel: load balancer, Let's Encrypt TLS, /healthz, and the process manager
(systemd on the box, AWS SSM-shaped restart when the provider is AWS).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from openmw.openvault.ship.detect import DetectedStack, detect_project
from openmw.openvault.ship.origin import origin_status
from openmw.openvault.ship.stacks import get_stack

ShipTarget = Literal[
    "openvault_hosted",
    "cursor_origin",
    "openship_cloud",
    "vps_ssh",
    "hetzner",
    "aws",
    "aws_guide",
    "local_demo",
]

RuntimeKind = Literal[
    "caddy_static",
    "vm_process",
    "docker_compose",
    "local_demo",
]

_REMOTE_TARGETS: frozenset[str] = frozenset(
    {
        "openvault_hosted",
        "vps_ssh",
        "hetzner",
        "aws",
        "aws_guide",
        "openship_cloud",
        "cursor_origin",
    }
)
_BYO_HOST_TARGETS: frozenset[str] = frozenset({"vps_ssh", "hetzner", "aws", "aws_guide"})


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
    ready_to_execute: bool
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
            f"A {hostname} → {vps_host or '<VPS_IP>'}",
            f"CNAME www.{hostname} → {hostname}",
        ]

    if kind == "static_http":
        runtime: RuntimeKind = "caddy_static"
        lb = "caddy"
        needs_vm = True
        needs_static = True
        recommended: ShipTarget = "openvault_hosted"
        detail = (
            "Log into OpenVault Service (not the laptop). We wrap AWS Lightsail "
            "or a VPS; Caddy file_server + Let's Encrypt. Not Vercel."
        )
    elif kind == "edge_http":
        runtime = "vm_process"
        lb = "caddy"
        needs_vm = True
        needs_static = False
        recommended = "openvault_hosted"
        detail = (
            "Log into OpenVault Service. systemd runs next start / node; "
            "Caddy reverse-proxies TLS on our wrapped AWS or VPS. Not Vercel."
        )
    elif kind == "container":
        runtime = "docker_compose"
        lb = "caddy"
        needs_vm = True
        needs_static = False
        recommended = "openvault_hosted"
        detail = (
            "Log into OpenVault Service. Compose/Dockerfile on the wrapped VM; "
            "Caddy terminates TLS and load-balances HTTP."
        )
    elif kind == "process":
        runtime = "vm_process"
        lb = "caddy"
        needs_vm = True
        needs_static = False
        recommended = "openvault_hosted"
        detail = (
            "Log into OpenVault Service. systemd (or AWS SSM) runs the process; "
            "Caddy is the load balancer. Own-server Fast SKU if they bring metal."
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
        needs_vm = False
        lb = "none"

    vm_needed = bool(needs_vm and chosen in _REMOTE_TARGETS)
    http_auto = chosen != "local_demo" and kind != "unknown"
    return HostPlan(
        git_target="cursor_origin",
        runtime=runtime,
        host_kind=kind,
        http_auto_update=http_auto,
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
    """Gates that must be green before auto-ship (detect, commands, domain, HTTP)."""
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
        cmd_detail = "static site — Caddy file_server"
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
            "title": "Public hostname + Caddy load balancer records",
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
    origin_required = (target or host.recommended_target) == "cursor_origin"
    gates.append(
        {
            "id": "origin",
            "title": "Cursor Origin git host (optional)",
            "status": "pass" if origin_ok else ("pending" if origin_required else "pass"),
            "detail": origin.get("detail") or "ORIGIN_MODE=simulate or origin CLI",
        }
    )
    if origin_required and not origin_ok:
        blockers.append("origin")

    runtime_detail = f"{host.runtime} lb={host.load_balancer} vm={host.needs_vm}"
    gates.append(
        {
            "id": "runtime",
            "title": "HTTP runtime (Caddy + systemd on VPS/Hetzner/AWS)",
            "status": "pass",
            "detail": runtime_detail,
        }
    )

    execute_needed = (target or host.recommended_target) in _BYO_HOST_TARGETS
    execute_ok = (not execute_needed) or bool(vps_host)
    hosted = (target or host.recommended_target) == "openvault_hosted"
    gates.append(
        {
            "id": "execute_host",
            "title": "VPS host for live apply",
            "status": "pass" if execute_ok else "pending",
            "detail": (
                vps_host
                if vps_host
                else (
                    "OpenVault Hosted assigns the box"
                    if hosted
                    else "plan ready; set vps_host or OPENVAULT_VPS_HOST to apply"
                )
            ),
        }
    )

    gates.append(
        {
            "id": "http_auto_update",
            "title": "HTTP auto-update",
            "status": "pass" if host.http_auto_update else "pending",
            "detail": (
                "git pull + systemd restart + Caddy reload; GET /healthz"
                if host.runtime != "local_demo"
                else "local demo has no public auto-update"
            ),
        }
    )

    if "origin" in blockers and origin.get("mode") == "simulate":
        blockers = [b for b in blockers if b != "origin"]
        for gate in gates:
            if gate["id"] == "origin":
                gate["status"] = "pass"
                gate["detail"] = "ORIGIN_MODE=simulate — plan only, no live push"

    ready = not blockers
    ready_to_execute = ready and execute_ok
    return ReadyReport(
        ready=ready,
        ready_to_execute=ready_to_execute,
        stack=stack.to_dict(),
        host=host.to_dict(),
        origin=origin,
        gates=gates,
        blockers=blockers,
    )
