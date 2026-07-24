"""Deploy gate pipeline — Cortex/AirGPT 'deploy to web' orchestration."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

from openmw.openvault.ship.detect import DetectedStack, detect_project
from openmw.openvault.ship.email_gates import check_email_auth
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.ship.openship import adapter_presence, build_openship_plan, execute_openship_plan
from openmw.openvault.paths import ensure_home
from openmw.openvault.ship.playwright_smoke import run_playwright_smoke
from openmw.openvault.vault.store import KeyVault

log = structlog.get_logger()

GateStatus = Literal["pass", "fail", "pending", "skipped"]


@dataclass
class Gate:
    id: str
    title: str
    status: GateStatus
    detail: str = ""
    blocker: bool = True


@dataclass
class DeployPlan:
    deploy_id: str
    intent: str
    source: str
    project_path: str
    subdomain: str
    stack: DetectedStack
    gates: list[Gate] = field(default_factory=list)
    ready_to_scale: bool = False
    console_url: str = "http://127.0.0.1:5000/#deploy"
    created_at: float = field(default_factory=time.time)
    openship: dict[str, Any] = field(default_factory=dict)
    smoke_url: str = ""
    ship_id: str | None = None
    smoke_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "deploy_id": self.deploy_id,
            "intent": self.intent,
            "source": self.source,
            "project_path": self.project_path,
            "subdomain": self.subdomain,
            "stack": self.stack.to_dict(),
            "gates": [asdict(g) for g in self.gates],
            "ready_to_scale": self.ready_to_scale,
            "console_url": self.console_url,
            "created_at": self.created_at,
            "openship": self.openship,
            "smoke_url": self.smoke_url,
            "ship_id": self.ship_id,
            "smoke_id": self.smoke_id,
        }


def _deploys_dir() -> Path:
    path = ensure_home() / "deploys"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_plan(plan: DeployPlan) -> Path:
    path = _deploys_dir() / f"{plan.deploy_id}.json"
    path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    return path


def load_plan(deploy_id: str) -> DeployPlan | None:
    path = _deploys_dir() / f"{deploy_id}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    stack = DetectedStack(**raw["stack"])
    gates = [Gate(**g) for g in raw.get("gates", [])]
    return DeployPlan(
        deploy_id=raw["deploy_id"],
        intent=raw.get("intent", "deploy_to_web"),
        source=raw.get("source", "unknown"),
        project_path=raw["project_path"],
        subdomain=raw.get("subdomain", ""),
        stack=stack,
        gates=gates,
        ready_to_scale=bool(raw.get("ready_to_scale")),
        console_url=raw.get("console_url", "http://127.0.0.1:5000/#deploy"),
        created_at=float(raw.get("created_at", time.time())),
        openship=dict(raw.get("openship", {})),
        smoke_url=raw.get("smoke_url", ""),
        ship_id=raw.get("ship_id"),
        smoke_id=raw.get("smoke_id"),
    )


def list_plans() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(_deploys_dir().glob("*.json"), reverse=True):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def build_deploy_plan(
    *,
    project_path: str,
    subdomain: str = "",
    intent: str = "deploy_to_web",
    source: str = "manual",
    vault: KeyVault | None = None,
    fallback: FallbackManager | None = None,
    cortex_online: bool = False,
    sending_ip: str | None = None,
    console_base: str = "http://127.0.0.1:5000",
    smoke_url: str = "",
    run_smoke: bool = False,
) -> DeployPlan:
    """Auto-detect stack and run merge gates for scale deploy."""
    stack = detect_project(project_path)
    deploy_id = uuid.uuid4().hex[:12]
    gates: list[Gate] = []

    # 1) Auto-detect
    if stack.primary == "unknown" or stack.confidence < 0.5:
        gates.append(
            Gate(
                "auto_detect",
                "Auto-detect project type",
                "fail",
                f"Could not detect stack at {stack.project_path}",
                True,
            )
        )
    else:
        gates.append(
            Gate(
                "auto_detect",
                "Auto-detect project type",
                "pass",
                (f"{stack.primary} conf={stack.confidence:.2f} signals={list(stack.signals)}"),
                True,
            )
        )

    # 2) Keys healthy
    key_detail = "no vault"
    key_status: GateStatus = "pending"
    if vault is not None:
        enabled = vault.enabled_ordered()
        ok = [k for k in enabled if k.precheck_status == "ok"]
        if not enabled:
            key_status = "fail"
            key_detail = "No API keys in OpenVault — add keys before scale deploy"
        elif ok:
            key_status = "pass"
            key_detail = f"{len(ok)}/{len(enabled)} keys precheck ok"
        else:
            key_status = "fail"
            key_detail = f"0/{len(enabled)} keys healthy — run precheck-all"
        if fallback is not None:
            hops = fallback.ordered_candidates()
            key_detail += f"; fallback pool={len(hops)}"
    gates.append(Gate("keys", "API keys healthy + fallback pool", key_status, key_detail, True))

    # 3) Cortex / Netie
    gates.append(
        Gate(
            "cortex",
            "Cortex / Netie Engine reachable",
            "pass" if cortex_online else "pending",
            (
                "online"
                if cortex_online
                else "offline — continue locally; reconnect for model assist"
            ),
            False,
        )
    )

    # 4) Subdomain
    if subdomain and "." in subdomain:
        gates.append(
            Gate(
                "subdomain",
                "Subdomain / public hostname",
                "pass",
                f"Target host {subdomain} (TLS via OpenShip/Let's Encrypt when adapter runs)",
                True,
            )
        )
    else:
        gates.append(
            Gate(
                "subdomain",
                "Subdomain / public hostname",
                "fail",
                "Provide subdomain like app.example.com",
                True,
            )
        )

    # 5) Secure email
    email_domain = ".".join(subdomain.split(".")[-2:]) if subdomain.count(".") >= 1 else subdomain
    if stack.needs_mail or intent == "deploy_to_web":
        email_results = check_email_auth(email_domain, sending_ip=sending_ip)
        hard_fails = [
            e for e in email_results if e.status == "fail" and e.name != "reputation_notes"
        ]
        if hard_fails:
            status: GateStatus = "fail"
        elif all(
            e.status in ("pass", "skipped") for e in email_results if e.name != "reputation_notes"
        ):
            status = "pass"
        else:
            status = "pending"
        detail = "; ".join(f"{e.name}={e.status}" for e in email_results)
        gates.append(
            Gate(
                "email_auth",
                "Secure email (SPF/DKIM/DMARC/PTR)",
                status,
                detail,
                blocker=stack.needs_mail,
            )
        )
    else:
        gates.append(
            Gate(
                "email_auth",
                "Secure email (SPF/DKIM/DMARC/PTR)",
                "skipped",
                "mail not required",
                False,
            )
        )

    # 6) Build plan
    if stack.suggested_build:
        gates.append(
            Gate(
                "build",
                "Build / rebuild plan",
                "pass",
                " → ".join(stack.suggested_build),
                True,
            )
        )
    else:
        gates.append(
            Gate("build", "Build / rebuild plan", "fail", "No suggested build commands", True)
        )

    # 7) Playwright / browser smoke — real runner when requested
    target_smoke = smoke_url or (f"https://{subdomain}" if subdomain and "." in subdomain else "")
    smoke_id: str | None = None
    if run_smoke and target_smoke:
        smoke = run_playwright_smoke(target_smoke)
        smoke_id = smoke.smoke_id
        gates.append(
            Gate(
                "playwright",
                "Playwright / browser smoke",
                smoke.status if smoke.status != "skipped" else "pending",
                f"{smoke.mode}: {smoke.detail} artifact={smoke.artifact_path}",
                False,
            )
        )
    else:
        mode = os.environ.get("OPENVAULT_PLAYWRIGHT_MODE", "auto")
        gates.append(
            Gate(
                "playwright",
                "Playwright / browser smoke",
                "pending",
                (
                    f"Call POST /api/deploy/{{id}}/playwright-smoke "
                    f"(mode={mode}, target={target_smoke or 'unset'})"
                ),
                False,
            )
        )

    # 8) OpenShip full clone plan
    ship = adapter_presence()
    ship_id: str | None = None
    if subdomain and stack.primary != "unknown":
        ship_plan = build_openship_plan(
            project_path=project_path,
            subdomain=subdomain,
            action="install",
            sending_ip=sending_ip,
        )
        ship_id = ship_plan.ship_id
        ship_status: GateStatus = "pass" if ship_plan.ready else "pending"
        if any(s.status == "fail" for s in ship_plan.steps):
            ship_status = "fail"
        gates.append(
            Gate(
                "openship",
                "OpenShip full plan (apps + services + TLS + mail)",
                ship_status,
                (
                    f"ship_id={ship_id} ready={ship_plan.ready} "
                    f"steps={len(ship_plan.steps)} mode={ship.get('mode')}"
                ),
                True,
            )
        )
        ship = {**ship, "ship_id": ship_id, "plan_ready": ship_plan.ready}
    elif ship["ready"]:
        gates.append(
            Gate(
                "openship",
                "OpenShip full plan (apps + services + TLS + mail)",
                "pass",
                f"cli={ship['cli_path'] or 'n/a'} api={ship['api_url'] or 'n/a'}",
                True,
            )
        )
    else:
        gates.append(
            Gate(
                "openship",
                "OpenShip full plan (apps + services + TLS + mail)",
                "pending",
                "Set OPENSHIP_MODE=simulate or OPENSHIP_CLI / OPENSHIP_URL",
                True,
            )
        )

    # 9) Roll updates
    gates.append(
        Gate(
            "roll",
            "Roll update / rollback ready",
            "pending",
            (
                "After green gates: install or update apps+services only "
                "(POST /api/deploy/{id}/execute)"
            ),
            True,
        )
    )

    blockers = [g for g in gates if g.blocker and g.status == "fail"]
    pending_blockers = [g for g in gates if g.blocker and g.status == "pending"]
    ready = len(blockers) == 0 and len(pending_blockers) == 0

    plan = DeployPlan(
        deploy_id=deploy_id,
        intent=intent,
        source=source,
        project_path=stack.project_path,
        subdomain=subdomain,
        stack=stack,
        gates=gates,
        ready_to_scale=ready,
        console_url=f"{console_base.rstrip('/')}/#deploy",
        openship=ship,
        smoke_url=target_smoke,
        ship_id=ship_id,
        smoke_id=smoke_id,
    )
    save_plan(plan)
    log.info(
        "openvault_deploy_plan",
        deploy_id=deploy_id,
        primary=stack.primary,
        ready=ready,
        source=source,
    )
    return plan


def run_deploy_smoke(plan: DeployPlan, *, url: str | None = None) -> DeployPlan:
    """Execute Playwright smoke against the deploy target and refresh the gate."""
    target = url or plan.smoke_url or (f"https://{plan.subdomain}" if plan.subdomain else "")
    smoke = run_playwright_smoke(target)
    plan.smoke_id = smoke.smoke_id
    plan.smoke_url = target
    updated = False
    for gate in plan.gates:
        if gate.id == "playwright":
            gate.status = smoke.status if smoke.status != "skipped" else "pending"
            gate.detail = f"{smoke.mode}: {smoke.detail} artifact={smoke.artifact_path}"
            updated = True
    if not updated:
        plan.gates.append(
            Gate(
                "playwright",
                "Playwright / browser smoke",
                smoke.status if smoke.status != "skipped" else "pending",
                f"{smoke.mode}: {smoke.detail} artifact={smoke.artifact_path}",
                False,
            )
        )
    _recompute_ready(plan)
    save_plan(plan)
    return plan


def execute_deploy(plan: DeployPlan, *, simulate: bool | None = None) -> DeployPlan:
    """Run the OpenShip executor for this deploy and mark roll gate."""
    if not plan.ship_id:
        ship_plan = build_openship_plan(
            project_path=plan.project_path,
            subdomain=plan.subdomain,
            action="install",
        )
        plan.ship_id = ship_plan.ship_id
    else:
        from openmw.openvault.ship.openship import load_ship_plan

        loaded = load_ship_plan(plan.ship_id)
        ship_plan = loaded or build_openship_plan(
            project_path=plan.project_path,
            subdomain=plan.subdomain,
            action="install",
        )
        plan.ship_id = ship_plan.ship_id

    executed = execute_openship_plan(ship_plan, simulate=simulate)
    plan.openship = {
        **plan.openship,
        **executed.adapter,
        "ship_id": executed.ship_id,
        "executed": executed.executed,
        "ready": executed.ready,
        "steps": [asdict(s) for s in executed.steps],
    }
    for gate in plan.gates:
        if gate.id == "openship":
            gate.status = "pass" if executed.ready else "fail"
            gate.detail = f"executed ship_id={executed.ship_id} ready={executed.ready}"
        if gate.id == "roll":
            gate.status = "pass" if executed.ready else "fail"
            gate.detail = (
                "roll complete — rollback via openship roll rollback"
                if executed.ready
                else "roll failed — see openship steps"
            )
    _recompute_ready(plan)
    save_plan(plan)
    return plan


def _recompute_ready(plan: DeployPlan) -> None:
    blockers = [g for g in plan.gates if g.blocker and g.status == "fail"]
    pending_blockers = [g for g in plan.gates if g.blocker and g.status == "pending"]
    plan.ready_to_scale = len(blockers) == 0 and len(pending_blockers) == 0
