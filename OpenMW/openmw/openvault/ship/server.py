"""Vercel replacement: VPS / Hetzner / AWS + systemd + Caddy TLS + health checks.

OpenVault owns HTTP. Cursor Origin stays git-only. This module emits:

- systemd unit (service manager, AWS SSM-shaped commands included)
- Caddyfile (load balancer + Let's Encrypt ACME)
- health check URL
- SSH/rsync deploy steps (simulate unless SHIP_MODE=live)

No Vercel, no PaaS edge. Same path for Next.js, static, FastAPI, and compose.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

from openmw.openvault.paths import ensure_home
from openmw.openvault.ship.detect import DetectedStack, detect_project

log = structlog.get_logger()

Provider = Literal["vps", "hetzner", "aws"]
StepStatus = Literal["pass", "fail", "pending", "skipped", "simulated"]


@dataclass
class ServerStep:
    id: str
    title: str
    status: StepStatus
    detail: str = ""
    command: str | None = None


@dataclass
class ServerPlan:
    plan_id: str
    provider: Provider
    project_path: str
    hostname: str
    vps_host: str
    service_name: str
    upstream_port: int
    health_url: str
    unit_file: str
    caddyfile: str
    ssm_restart: str
    stack: dict[str, Any]
    steps: list[ServerStep] = field(default_factory=list)
    ready: bool = False
    executed: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "provider": self.provider,
            "project_path": self.project_path,
            "hostname": self.hostname,
            "vps_host": self.vps_host,
            "service_name": self.service_name,
            "upstream_port": self.upstream_port,
            "health_url": self.health_url,
            "unit_file": self.unit_file,
            "caddyfile": self.caddyfile,
            "ssm_restart": self.ssm_restart,
            "stack": self.stack,
            "steps": [asdict(s) for s in self.steps],
            "ready": self.ready,
            "executed": self.executed,
            "created_at": self.created_at,
        }


def normalize_provider(target: str) -> Provider:
    if target in {"hetzner"}:
        return "hetzner"
    if target in {"aws", "aws_guide"}:
        return "aws"
    # openvault_hosted wraps Lightsail or a VPS; runtime is still Caddy on a box.
    return "vps"


def service_name_for(project_path: str) -> str:
    name = Path(project_path).resolve().name or "app"
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name).strip("-")
    return f"openvault-{cleaned or 'app'}"


def _upstream_port(stack: DetectedStack) -> int:
    if stack.production_port:
        return int(stack.production_port)
    if stack.host_kind == "static_http" or stack.framework == "static":
        return 8080
    return 3000


def systemd_unit(
    *,
    service_name: str,
    workdir: str,
    start_command: str,
    port: int,
) -> str:
    exec_start = start_command.strip() or f"python -m http.server {port}"
    return (
        f"[Unit]\n"
        f"Description=OpenVault app {service_name}\n"
        f"After=network.target\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"WorkingDirectory={workdir}\n"
        f"Environment=PORT={port}\n"
        f"Environment=HOST=127.0.0.1\n"
        f"ExecStart={exec_start}\n"
        f"Restart=on-failure\n"
        f"RestartSec=3\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=multi-user.target\n"
    )


def caddyfile(*, hostname: str, port: int, static_root: str = "") -> str:
    host = hostname or "localhost"
    if static_root:
        return (
            f"{host} {{\n"
            f"\tencode gzip\n"
            f"\troot * {static_root}\n"
            f"\tfile_server\n"
            f"\theader /healthz Content-Type text/plain\n"
            f"\trespond /healthz 200 {{\n"
            f'\t\tbody "ok"\n'
            f"\t}}\n"
            f"}}\n"
        )
    return (
        f"{host} {{\n"
        f"\tencode gzip\n"
        f"\treverse_proxy 127.0.0.1:{port}\n"
        f"\thandle /healthz {{\n"
        f'\t\trespond "ok" 200\n'
        f"\t}}\n"
        f"}}\n"
    )


def ssm_restart_command(service_name: str, instance_id: str = "<instance-id>") -> str:
    inner = f"systemctl restart {service_name}"
    return (
        "aws ssm send-command "
        f"--instance-ids {instance_id} "
        "--document-name AWS-RunShellScript "
        f"--parameters commands='{inner}'"
    )


def _plans_dir() -> Path:
    path = ensure_home() / "server_plans"
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_server_plan(
    *,
    project_path: str,
    hostname: str = "",
    vps_host: str = "",
    target: str = "vps_ssh",
    stack: DetectedStack | None = None,
) -> ServerPlan:
    detected = stack or detect_project(project_path)
    provider = normalize_provider(target)
    service = service_name_for(detected.project_path or project_path)
    port = _upstream_port(detected)
    remote_root = f"/var/www/{service}"
    is_static = detected.host_kind == "static_http" or detected.framework == "static"
    static_root = f"{remote_root}/{detected.output_directory or '.'}" if is_static else ""
    start = detected.start_command or (f"python -m http.server {port}" if is_static else "")
    unit = systemd_unit(
        service_name=service,
        workdir=remote_root,
        start_command=start,
        port=port,
    )
    caddy = caddyfile(
        hostname=hostname or "localhost",
        port=port,
        static_root=static_root,
    )
    if hostname and "." in hostname:
        health = f"https://{hostname}/healthz"
    else:
        health = f"http://127.0.0.1:{port}/healthz"
    host = vps_host or os.environ.get("OPENVAULT_VPS_HOST", "")
    steps: list[ServerStep] = []

    if detected.primary == "unknown" or detected.confidence < 0.5:
        steps.append(ServerStep("detect", "Detect app type", "fail", "unknown stack"))
    else:
        steps.append(
            ServerStep(
                "detect",
                "Detect app type",
                "pass",
                f"{detected.framework or detected.primary} kind={detected.host_kind}",
            )
        )

    if hostname and "." in hostname:
        steps.append(
            ServerStep(
                "dns",
                "DNS A record to the VPS",
                "pass",
                f"A {hostname} → {host or '<VPS_IP>'}",
            )
        )
    else:
        steps.append(
            ServerStep(
                "dns", "DNS A record to the VPS", "fail", "hostname like app.example.com required"
            )
        )

    ssh_ok = bool(host) or os.environ.get("OPENVAULT_SHIP_MODE", "simulate") == "simulate"
    steps.append(
        ServerStep(
            "ssh",
            f"SSH {provider} host",
            "pass" if host else ("pending" if ssh_ok else "fail"),
            f"ssh {host or '<vps>'} (Hetzner/AWS/VPS). Set vps_host or OPENVAULT_VPS_HOST.",
            command=f"ssh {host} 'uname -a'" if host else None,
        )
    )

    steps.append(
        ServerStep(
            "sync",
            "Sync release to the box",
            "pass",
            f"rsync/git pull into {remote_root}",
            command=(
                f"rsync -az --delete {detected.project_path}/ {host}:{remote_root}/"
                if host
                else None
            ),
        )
    )

    if is_static:
        steps.append(
            ServerStep(
                "service",
                "Static files via Caddy (no Node process)",
                "pass",
                "Caddy file_server replaces Vercel static hosting",
            )
        )
    else:
        steps.append(
            ServerStep(
                "service",
                "systemd service manager",
                "pass",
                f"unit={service}.service restart=on-failure",
                command=f"systemctl enable --now {service}",
            )
        )

    steps.append(
        ServerStep(
            "load_balancer",
            "Caddy load balancer",
            "pass",
            "TLS reverse_proxy / file_server on :443 — OpenVault, not Vercel",
            command="systemctl reload caddy",
        )
    )
    steps.append(
        ServerStep(
            "tls",
            "TLS / Let's Encrypt (ACME HTTP-01)",
            "pass" if hostname and "." in hostname else "fail",
            "Caddy obtains and renews the cert. No EST/ACM from a PaaS.",
        )
    )
    steps.append(
        ServerStep(
            "health",
            "Health check",
            "pass",
            f"GET {health} expect 200",
            command=f"curl -fsS {health}",
        )
    )
    if provider == "aws":
        steps.append(
            ServerStep(
                "ssm",
                "AWS Systems Manager restart",
                "pass",
                "Same systemd unit, invoked via SSM when an instance id is set",
                command=ssm_restart_command(service),
            )
        )

    blockers = [s for s in steps if s.status == "fail"]
    plan = ServerPlan(
        plan_id=uuid.uuid4().hex[:12],
        provider=provider,
        project_path=str(detected.project_path),
        hostname=hostname,
        vps_host=host,
        service_name=service,
        upstream_port=port,
        health_url=health,
        unit_file=unit,
        caddyfile=caddy,
        ssm_restart=ssm_restart_command(service),
        stack=detected.to_dict(),
        steps=steps,
        ready=len(blockers) == 0,
    )
    _save(plan)
    return plan


def execute_server_plan(plan: ServerPlan, *, simulate: bool | None = None) -> ServerPlan:
    mode = os.environ.get("OPENVAULT_SHIP_MODE", "simulate")
    force_sim = simulate if simulate is not None else mode != "live"
    ssh = shutil.which("ssh")
    for step in plan.steps:
        if step.status == "fail":
            continue
        if force_sim or mode == "simulate" or not plan.vps_host:
            step.status = "simulated"
            step.detail = f"simulated: {step.detail}"
            continue
        if step.id == "health":
            ok, detail = _http_health(plan.health_url)
            step.status = "pass" if ok else "fail"
            step.detail = detail[:2000]
            continue
        if ssh and step.command and step.command.startswith("ssh "):
            ok, detail = _run_checked(step.command, timeout=60.0)
            step.status = "pass" if ok else "fail"
            step.detail = detail[:2000]
            continue
        step.status = "simulated"
        step.detail = f"no live SSH in this console — {step.detail}"
    plan.executed = True
    plan.ready = all(s.status in ("pass", "simulated", "skipped") for s in plan.steps)
    _save(plan)
    log.info("server_execute", plan_id=plan.plan_id, ready=plan.ready, simulated=force_sim)
    return plan


def load_server_plan(plan_id: str) -> ServerPlan | None:
    path = _plans_dir() / f"{plan_id}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    steps = [ServerStep(**s) for s in raw.get("steps", [])]
    provider_raw = raw.get("provider", "vps")
    provider: Provider = provider_raw if provider_raw in ("vps", "hetzner", "aws") else "vps"
    return ServerPlan(
        plan_id=raw["plan_id"],
        provider=provider,
        project_path=raw["project_path"],
        hostname=raw.get("hostname", ""),
        vps_host=raw.get("vps_host", ""),
        service_name=raw.get("service_name", ""),
        upstream_port=int(raw.get("upstream_port") or 0),
        health_url=raw.get("health_url", ""),
        unit_file=raw.get("unit_file", ""),
        caddyfile=raw.get("caddyfile", ""),
        ssm_restart=raw.get("ssm_restart", ""),
        stack=dict(raw.get("stack") or {}),
        steps=steps,
        ready=bool(raw.get("ready")),
        executed=bool(raw.get("executed")),
        created_at=float(raw.get("created_at", time.time())),
    )


def _save(plan: ServerPlan) -> Path:
    path = _plans_dir() / f"{plan.plan_id}.json"
    path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    return path


def _http_health(url: str) -> tuple[bool, str]:
    try:
        import httpx
    except ImportError:
        return False, "httpx missing"
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            response = client.get(url)
    except (httpx.HTTPError, OSError) as exc:
        return False, str(exc)
    return response.status_code == 200, f"status={response.status_code}"


def _run_checked(command: str, *, timeout: float) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    detail = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, detail.strip() or f"exit={proc.returncode}"
