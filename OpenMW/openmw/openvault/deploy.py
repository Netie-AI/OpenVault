"""Deploy gate pipeline — Cortex/AirGPT 'deploy to web' orchestration."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

from openmw.openvault.detect import DetectedStack, detect_project
from openmw.openvault.email_gates import check_email_auth
from openmw.openvault.fallback import FallbackManager
from openmw.openvault.paths import ensure_home
from openmw.openvault.vault import KeyVault

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
    )


def list_plans() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(_deploys_dir().glob("*.json"), reverse=True):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def _openship_presence() -> dict[str, Any]:
    cli = os.environ.get("OPENSHIP_CLI", "openship")
    url = os.environ.get("OPENSHIP_URL", "").rstrip("/")
    which = shutil.which(cli)
    return {
        "cli_configured": cli,
        "cli_found": which is not None,
        "cli_path": which,
        "api_url": url or None,
        "ready": which is not None or bool(url),
    }


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

    # 7) Playwright / browser smoke
    playwright = shutil.which("playwright") is not None
    gates.append(
        Gate(
            "playwright",
            "Playwright / MCP browser smoke",
            "pass" if playwright else "pending",
            (
                "playwright CLI found — run smoke after roll"
                if playwright
                else (
                    "Playwright MCP not installed — gate pending; attach fail logs when available"
                )
            ),
            False,
        )
    )

    # 8) OpenShip adapter / scale executor
    ship = _openship_presence()
    if ship["ready"]:
        gates.append(
            Gate(
                "openship",
                "OpenShip adapter (apps + services install/update)",
                "pass",
                f"cli={ship['cli_path'] or 'n/a'} api={ship['api_url'] or 'n/a'}",
                True,
            )
        )
    else:
        gates.append(
            Gate(
                "openship",
                "OpenShip adapter (apps + services install/update)",
                "pending",
                "Set OPENSHIP_CLI or OPENSHIP_URL to execute scale-only deploy",
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
                "(no rebuild unless detect changed)"
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
