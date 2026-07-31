"""FreeBuild control plane — in-repo under OpenVault (not a separate AirGPT product).

FreeBuild operator loop lives here so custody + deploy gate stay with OpenVault:
  subdomain → TLS plan → build → mail DNS → apps/services install|update → roll/rollback

AirGPT/FreeIDE are thin clients: request ship via OpenVault APIs; do not re-host this loop.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

from openmw.openvault.ship.detect import DetectedStack, detect_project
from openmw.openvault.ship.email_gates import check_email_auth
from openmw.openvault.ship.openship_client import OpenShipClient, adapter_status
from openmw.openvault.paths import ensure_home

log = structlog.get_logger()

StepStatus = Literal["pass", "fail", "pending", "skipped", "simulated"]
Action = Literal["install", "update", "rollback"]


@dataclass
class ShipStep:
    id: str
    title: str
    status: StepStatus
    detail: str = ""
    command: str | None = None


@dataclass
class OpenShipPlan:
    ship_id: str
    project_path: str
    subdomain: str
    action: Action
    stack: DetectedStack
    steps: list[ShipStep] = field(default_factory=list)
    ready: bool = False
    executed: bool = False
    created_at: float = field(default_factory=time.time)
    adapter: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ship_id": self.ship_id,
            "project_path": self.project_path,
            "subdomain": self.subdomain,
            "action": self.action,
            "stack": self.stack.to_dict(),
            "steps": [asdict(s) for s in self.steps],
            "ready": self.ready,
            "executed": self.executed,
            "created_at": self.created_at,
            "adapter": self.adapter,
        }


def _ships_dir() -> Path:
    path = ensure_home() / "openship"
    path.mkdir(parents=True, exist_ok=True)
    return path


def adapter_presence() -> dict[str, Any]:
    """Prefer real FreeBuild API status; keep legacy keys for older UI."""
    status = adapter_status()
    return {
        "cli_configured": os.environ.get("OPENSHIP_CLI", "openship"),
        "cli_found": bool(status.get("cli_found")),
        "cli_path": status.get("cli_path"),
        "api_url": status.get("api_url"),
        "mode": status.get("mode", "auto"),
        "effective": status.get("effective"),
        "api_ready": bool(status.get("api_ready")),
        "ready": status.get("effective") in ("api", "cli", "simulate"),
        "in_repo_clone": True,
        "honest": status.get("honest"),
        "docs": status.get("docs"),
        "install_hint": status.get("install_hint"),
    }


def build_openship_plan(
    *,
    project_path: str,
    subdomain: str,
    action: Action = "install",
    sending_ip: str | None = None,
) -> OpenShipPlan:
    """Full FreeBuild checklist — every surface the scale deploy needs."""
    stack = detect_project(project_path)
    ship_id = uuid.uuid4().hex[:12]
    adapter = adapter_presence()
    steps: list[ShipStep] = []

    # 1) Project detect
    if stack.primary == "unknown" or stack.confidence < 0.5:
        steps.append(
            ShipStep(
                "detect",
                "Detect app + services",
                "fail",
                f"unknown stack at {stack.project_path}",
            )
        )
    else:
        steps.append(
            ShipStep(
                "detect",
                "Detect app + services",
                "pass",
                f"{stack.primary} conf={stack.confidence:.2f}",
            )
        )

    # 2) Subdomain
    if subdomain and "." in subdomain:
        steps.append(
            ShipStep(
                "subdomain",
                "Subdomain routing",
                "pass",
                f"host={subdomain}",
                command=f"openship dns ensure --host {subdomain}",
            )
        )
    else:
        steps.append(
            ShipStep(
                "subdomain",
                "Subdomain routing",
                "fail",
                "subdomain required (app.example.com)",
            )
        )

    # 3) TLS
    steps.append(
        ShipStep(
            "tls",
            "TLS / Let's Encrypt plan",
            "pass" if subdomain and "." in subdomain else "fail",
            (
                f"Issue cert for {subdomain} via ACME when executor runs"
                if subdomain
                else "no host for certificate"
            ),
            command=f"openship tls issue --host {subdomain}" if subdomain else None,
        )
    )

    # 4) Mail auth (SPF/DKIM/DMARC/PTR)
    email_domain = ".".join(subdomain.split(".")[-2:]) if subdomain.count(".") >= 1 else subdomain
    email_results = check_email_auth(email_domain, sending_ip=sending_ip)
    hard = [e for e in email_results if e.status == "fail" and e.name != "reputation_notes"]
    if hard:
        mail_status: StepStatus = "fail"
    elif all(
        e.status in ("pass", "skipped") for e in email_results if e.name != "reputation_notes"
    ):
        mail_status = "pass"
    else:
        mail_status = "pending"
    steps.append(
        ShipStep(
            "mail",
            "Secure email DNS (SPF/DKIM/DMARC/PTR)",
            mail_status,
            "; ".join(f"{e.name}={e.status}" for e in email_results),
            command=f"openship mail ensure --domain {email_domain}",
        )
    )

    # 5) Build
    if stack.suggested_build:
        steps.append(
            ShipStep(
                "build",
                "Build / rebuild",
                "pass",
                " → ".join(stack.suggested_build),
                command=" && ".join(stack.suggested_build),
            )
        )
    else:
        steps.append(ShipStep("build", "Build / rebuild", "fail", "no suggested build commands"))

    # 6) Apps + services
    steps.append(
        ShipStep(
            "apps_services",
            f"Apps + services {action}",
            "pass" if adapter["ready"] else "pending",
            (
                f"scale-only {action} via "
                f"{'CLI' if adapter['cli_found'] else 'API' if adapter['api_url'] else 'simulate'}"
            ),
            command=f"openship apps {action} --path {stack.project_path} --host {subdomain}",
        )
    )

    # 7) Roll / rollback ready
    steps.append(
        ShipStep(
            "roll",
            "Roll / rollback readiness",
            "pass",
            "previous release retained for rollback when executor records release id",
            command="openship roll status",
        )
    )

    blockers = [s for s in steps if s.status == "fail"]
    pending = [s for s in steps if s.status == "pending"]
    plan = OpenShipPlan(
        ship_id=ship_id,
        project_path=stack.project_path,
        subdomain=subdomain,
        action=action,
        stack=stack,
        steps=steps,
        ready=len(blockers) == 0 and len(pending) == 0,
        adapter=adapter,
    )
    save_ship_plan(plan)
    return plan


def save_ship_plan(plan: OpenShipPlan) -> Path:
    path = _ships_dir() / f"{plan.ship_id}.json"
    path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    return path


def load_ship_plan(ship_id: str) -> OpenShipPlan | None:
    path = _ships_dir() / f"{ship_id}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    stack = DetectedStack(**raw["stack"])
    steps = [ShipStep(**s) for s in raw.get("steps", [])]
    action_raw = raw.get("action", "install")
    action: Action = (
        action_raw if action_raw in ("install", "update", "rollback") else "install"
    )
    return OpenShipPlan(
        ship_id=raw["ship_id"],
        project_path=raw["project_path"],
        subdomain=raw.get("subdomain", ""),
        action=action,
        stack=stack,
        steps=steps,
        ready=bool(raw.get("ready")),
        executed=bool(raw.get("executed")),
        created_at=float(raw.get("created_at", time.time())),
        adapter=dict(raw.get("adapter", {})),
    )


def list_ship_plans() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(_ships_dir().glob("*.json"), reverse=True):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def _run_checked(cmd: list[str], *, timeout: float = 120.0) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    detail = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, detail.strip() or f"exit={proc.returncode}"


def execute_openship_plan(
    plan: OpenShipPlan,
    *,
    simulate: bool | None = None,
    deploy_target: str = "cloud",
    project_id: str | None = None,
    branch: str = "main",
    cloud_tier: str = "low",
    server_id: str | None = None,
    github_url: str | None = None,
) -> OpenShipPlan:
    """Execute via FreeBuild HTTP API, CLI, or local simulator.

    Prefer ``OPENSHIP_URL`` + ``OPENSHIP_TOKEN`` → ``POST /deployments/build/access``.
    """
    adapter = adapter_presence()
    mode = str(adapter.get("effective") or adapter.get("mode") or "simulate")
    force_sim = (
        simulate
        if simulate is not None
        else (mode == "simulate" or not (adapter.get("api_ready") or adapter.get("cli_found")))
    )

    if not force_sim and adapter.get("api_ready"):
        client = OpenShipClient()
        body: dict[str, Any] = {
            "branch": branch,
            "deployTarget": deploy_target if deploy_target in ("local", "server", "cloud") else "cloud",
            "envVars": {},
        }
        if project_id:
            body["projectId"] = project_id
        if server_id:
            body["serverId"] = server_id
        if deploy_target == "cloud":
            body["cloudResourceTier"] = cloud_tier
        if plan.subdomain:
            body["publicEndpoints"] = [
                {"domainType": "custom" if "." in plan.subdomain else "free", "domain": plan.subdomain}
            ]
        if github_url:
            prep = client.prepare({"repoUrl": github_url, "branch": branch})
            for step in plan.steps:
                if step.id == "detect":
                    step.status = "pass" if prep.get("ok", True) else "fail"
                    step.detail = json.dumps(prep)[:2000]
        result = client.build_access(body) if project_id else {
            "ok": False,
            "error": (
                "project_id required for FreeBuild build/access — "
                "create project in FreeBuild UI or pass projectId"
            ),
            "prepare_hint": github_url or plan.project_path,
        }
        client.close()
        dep_id = result.get("deployment_id") or result.get("deploymentId")
        status = int(result.get("http_status", 500) or 500)
        ok = bool(dep_id) or (
            status < 400 and bool(result.get("ok", False)) and "error" not in result
        )
        for step in plan.steps:
            if step.status == "fail":
                continue
            step.status = "pass" if ok else "fail"
            step.detail = (json.dumps(result) if isinstance(result, dict) else str(result))[:2000]
        plan.executed = True
        plan.ready = ok
        plan.adapter = {**adapter, "last_result": result, "deployment_id": dep_id}
        save_ship_plan(plan)
        log.info("openship_execute_api", ship_id=plan.ship_id, ready=plan.ready, deployment_id=dep_id)
        return plan

    for step in plan.steps:
        if step.status == "fail":
            continue
        if force_sim or mode == "simulate":
            step.status = "simulated"
            step.detail = f"simulated: {step.detail}"
            continue
        if adapter.get("cli_found") and step.command:
            ok, detail = _run_checked(step.command.split(), timeout=180.0)
            step.status = "pass" if ok else "fail"
            step.detail = detail[:2000]
        elif adapter.get("api_url"):
            step.status = "pending"
            step.detail = "Set OPENSHIP_TOKEN — API URL alone cannot authenticate"
        else:
            step.status = "simulated"
            step.detail = f"no CLI/API — simulated {step.id}"

    plan.executed = True
    plan.ready = all(s.status in ("pass", "simulated", "skipped") for s in plan.steps)
    plan.adapter = adapter
    save_ship_plan(plan)
    log.info(
        "openship_execute",
        ship_id=plan.ship_id,
        ready=plan.ready,
        simulated=force_sim,
    )
    return plan
