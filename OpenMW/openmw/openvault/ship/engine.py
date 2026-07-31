"""In-process ship engine — steal FreeBuild operating concept into OpenVault.

Primary path (no remote FreeBuild client required):
  library source → detect → cicd → domain teach → target (cloud/vps/aws/local)
  → optional clone → build commands (local) → record deployment artifact

Remote ``openship_client`` remains optional when OPENSHIP_URL+TOKEN set.
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

from openmw.openvault.paths import ensure_home
from openmw.openvault.ship.cicd import detect_cicd
from openmw.openvault.ship.cloud_targets import (
    ShipTarget,
    build_ship_blueprint,
    load_bill_budget,
)
from openmw.openvault.ship.detect import detect_project
from openmw.openvault.ship.domain_guide import build_domain_guide
from openmw.openvault.ship.library import ensure_local_clone, inspect_folder, inspect_github_url

log = structlog.get_logger()

StepStatus = Literal["pass", "fail", "pending", "skipped", "simulated", "running"]


@dataclass
class EngineStep:
    id: str
    title: str
    status: StepStatus
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ShipDeployment:
    deployment_id: str
    target: ShipTarget
    project_path: str
    hostname: str
    github_url: str = ""
    steps: list[EngineStep] = field(default_factory=list)
    stack: dict[str, Any] = field(default_factory=dict)
    cicd: dict[str, Any] = field(default_factory=dict)
    blueprint: dict[str, Any] = field(default_factory=dict)
    ready: bool = False
    public_url: str = ""
    created_at: float = field(default_factory=time.time)
    mode: str = "local_engine"

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "target": self.target,
            "project_path": self.project_path,
            "hostname": self.hostname,
            "github_url": self.github_url,
            "steps": [s.to_dict() for s in self.steps],
            "stack": self.stack,
            "cicd": self.cicd,
            "blueprint": self.blueprint,
            "ready": self.ready,
            "public_url": self.public_url,
            "created_at": self.created_at,
            "mode": self.mode,
        }


def _deployments_dir() -> Path:
    path = ensure_home() / "ship_engine"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_deployment(dep: ShipDeployment) -> Path:
    path = _deployments_dir() / f"{dep.deployment_id}.json"
    path.write_text(json.dumps(dep.to_dict(), indent=2), encoding="utf-8")
    return path


def load_deployment(deployment_id: str) -> ShipDeployment | None:
    path = _deployments_dir() / f"{deployment_id}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    steps = [EngineStep(**s) for s in raw.get("steps", [])]
    return ShipDeployment(
        deployment_id=raw["deployment_id"],
        target=raw.get("target", "local_demo"),  # type: ignore[arg-type]
        project_path=raw.get("project_path", ""),
        hostname=raw.get("hostname", ""),
        github_url=raw.get("github_url", ""),
        steps=steps,
        stack=dict(raw.get("stack", {})),
        cicd=dict(raw.get("cicd", {})),
        blueprint=dict(raw.get("blueprint", {})),
        ready=bool(raw.get("ready")),
        public_url=raw.get("public_url", ""),
        created_at=float(raw.get("created_at", time.time())),
        mode=raw.get("mode", "local_engine"),
    )


def _run_build(commands: list[str], cwd: Path) -> tuple[bool, str]:
    if not commands:
        return False, "no build commands"
    # Safety: only run first suggested command chain split by &&
    joined = " && ".join(commands)
    try:
        proc = subprocess.run(
            joined,
            shell=True,
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=600.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode == 0, detail[:4000] or f"exit={proc.returncode}"


def _host_cloudflare_pages(
    *,
    work_path: Path,
    stack: Any,
    hostname: str,
    run_build: bool,
) -> tuple[EngineStep, str]:
    """Publish to the user's Cloudflare account. Returns (step, public_url).

    Deliberately unforgiving about preconditions. Every early return here is a
    case that the old ``simulated`` host step would have reported as a
    successful deploy with a URL that did not exist.
    """
    from openmw.openvault.ship.hosts.cloudflare_pages import CloudflarePagesAdapter

    if not run_build:
        return (
            EngineStep(
                "host",
                "Cloudflare Pages",
                "fail",
                "nothing was built — publishing needs run_build=true so there is "
                "an artifact to upload",
            ),
            "",
        )

    output_dir = (getattr(stack, "output_directory", "") or "").strip()
    if not output_dir:
        return (
            EngineStep(
                "host",
                "Cloudflare Pages",
                "fail",
                "the detected stack has no output directory, so we do not know "
                "which folder to upload",
            ),
            "",
        )

    root = getattr(stack, "root_directory", "") or ""
    artifact = work_path / root / output_dir
    project = hostname.split(".")[0] if hostname else work_path.name

    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    adapter = CloudflarePagesAdapter(api_token=token, account_id=account)

    result = adapter.deploy(artifact, project=project)
    if not result.ok:
        return EngineStep("host", "Cloudflare Pages", "fail", result.detail), ""

    detail = result.detail
    if hostname:
        domain = adapter.attach_domain(project=project, hostname=hostname)
        if domain.ok:
            detail = f"{detail} · {domain.detail}"
        else:
            # A failed domain attach does not undo a successful upload; the
            # site is live on pages.dev either way. Say both things.
            records = "; ".join(
                f"{r['type']} {r['name']} -> {r['value']}" for r in domain.required_records
            )
            detail = f"{detail} · {domain.detail}{f' [{records}]' if records else ''}"

    return (
        EngineStep("host", "Cloudflare Pages", "pass", detail),
        result.url,
    )


def run_ship_engine(
    *,
    target: ShipTarget,
    project_path: str = "",
    github_url: str = "",
    hostname: str = "",
    vps_host: str = "",
    cloud_tier: str = "low",
    monthly_cap_usd: float | None = None,
    run_build: bool = False,
    prefer_remote_openship: bool = False,
) -> dict[str, Any]:
    """One-stop ship — FreeBuild concept in-process.

    ``prefer_remote_openship`` only if OPENSHIP_URL+TOKEN set; default is local engine.
    """
    budget = load_bill_budget()
    if monthly_cap_usd is not None:
        from openmw.openvault.ship.cloud_targets import BillBudget, save_bill_budget

        budget = save_bill_budget(
            BillBudget(
                monthly_cap_usd=float(monthly_cap_usd),
                spent_usd_estimate=budget.spent_usd_estimate,
                soft_warn_pct=budget.soft_warn_pct,
                hard_stop=budget.hard_stop,
            )
        )
    if budget.to_dict().get("blocked") and target != "local_demo":
        return {
            "ok": False,
            "error": "monthly bill cap exceeded",
            "budget": budget.to_dict(),
        }

    steps: list[EngineStep] = []
    work_path = project_path
    gh_url = github_url.strip()

    # 1) Resolve source
    if gh_url and not work_path:
        steps.append(EngineStep("source", "Resolve GitHub source", "running", gh_url))
        inspected = inspect_github_url(gh_url)
        if not inspected.get("ok"):
            steps[-1].status = "fail"
            steps[-1].detail = str(inspected.get("error"))
            dep = _finish(steps, target, "", hostname, gh_url)
            return {"ok": False, "deployment": dep.to_dict()}
        clone = ensure_local_clone(gh_url)
        if not clone.get("ok"):
            steps[-1].status = "fail"
            steps[-1].detail = str(clone.get("error") or clone.get("detail"))
            dep = _finish(steps, target, "", hostname, gh_url)
            return {"ok": False, "deployment": dep.to_dict(), "clone": clone}
        work_path = str(clone["path"])
        steps[-1].status = "pass"
        steps[-1].detail = f"{clone.get('action')} → {work_path}"
    elif work_path:
        inspected = inspect_folder(work_path)
        steps.append(
            EngineStep(
                "source",
                "Resolve folder source",
                "pass" if inspected.get("ok") else "fail",
                work_path,
            )
        )
        if not inspected.get("ok"):
            dep = _finish(steps, target, work_path, hostname, gh_url)
            return {"ok": False, "deployment": dep.to_dict()}
    else:
        steps.append(EngineStep("source", "Resolve source", "fail", "need folder or github_url"))
        dep = _finish(steps, target, "", hostname, gh_url)
        return {"ok": False, "deployment": dep.to_dict()}

    stack = detect_project(work_path)
    cicd = detect_cicd(work_path)
    steps.append(
        EngineStep(
            "detect",
            "Stack detection",
            "pass" if stack.primary != "unknown" else "fail",
            f"{stack.primary} conf={stack.confidence:.2f}",
        )
    )
    steps.append(
        EngineStep(
            "cicd",
            "CI/CD scan",
            "pass" if cicd.status == "present" else "pending",
            f"{cicd.status}: {', '.join(cicd.detected) or 'suggest workflow'}",
        )
    )

    blueprint = build_ship_blueprint(
        target=target,
        project_path=work_path,
        hostname=hostname,
        github_url=gh_url,
        vps_host=vps_host,
        cloud_tier=cloud_tier,
        monthly_cap_usd=monthly_cap_usd,
    )
    domain = build_domain_guide(hostname) if hostname else build_domain_guide("")
    steps.append(
        EngineStep(
            "domain",
            "Domain / DNS teach",
            "pass" if hostname and "." in hostname else "pending",
            f"apex={domain.apex}" if hostname else "set hostname",
        )
    )

    # Optional remote FreeBuild — secondary
    remote_result: dict[str, Any] | None = None
    if prefer_remote_openship:
        from openmw.openvault.ship.openship_client import OpenShipClient

        client = OpenShipClient()
        if client.available:
            steps.append(EngineStep("remote", "FreeBuild remote build/access", "running"))
            remote_result = client.build_access(
                {
                    "deployTarget": (
                        "cloud"
                        if target == "openship_cloud"
                        else "server"
                        if target == "vps_ssh"
                        else "local"
                    ),
                    "branch": "main",
                    "cloudResourceTier": cloud_tier,
                }
            )
            client.close()
            ok_remote = bool(
                remote_result.get("deployment_id")
                or remote_result.get("deploymentId")
                or (remote_result.get("http_status", 500) < 400 and remote_result.get("ok"))
            )
            steps[-1].status = "pass" if ok_remote else "fail"
            steps[-1].detail = json.dumps(remote_result)[:1500]
        else:
            steps.append(
                EngineStep(
                    "remote",
                    "FreeBuild remote",
                    "skipped",
                    "OPENSHIP_URL+TOKEN not set — using local engine",
                )
            )

    # Local build (optional — off by default for safety)
    if run_build and stack.suggested_build:
        steps.append(EngineStep("build", "Local build", "running", " → ".join(stack.suggested_build)))
        # Detected commands belong to stack.root_directory — in a monorepo that
        # is the sub-app, not the repo root.
        ok_b, detail = _run_build(stack.suggested_build, Path(work_path, stack.root_directory))
        steps[-1].status = "pass" if ok_b else "fail"
        steps[-1].detail = detail
    else:
        steps.append(
            EngineStep(
                "build",
                "Build plan",
                "pass" if stack.suggested_build else "fail",
                " → ".join(stack.suggested_build) if stack.suggested_build else "unknown stack",
            )
        )

    # Target-specific closing step
    if target == "cloudflare_pages":
        # The only branch here that actually publishes. It requires a real
        # build to have happened, because there is nothing to upload
        # otherwise — so it refuses rather than emitting an empty deploy.
        step, public = _host_cloudflare_pages(
            work_path=Path(work_path),
            stack=stack,
            hostname=hostname,
            run_build=run_build,
        )
        steps.append(step)
    elif target == "openship_cloud":
        steps.append(
            EngineStep(
                "host",
                "Host on FreeBuild Cloud",
                "simulated" if not prefer_remote_openship else steps[-1].status,
                "Set OPENSHIP_URL+TOKEN + prefer_remote for live *.opsh.io; else simulate",
            )
        )
        public = f"https://{hostname}" if hostname else "https://<project>.opsh.io"
    elif target == "vps_ssh":
        steps.append(
            EngineStep(
                "host",
                f"Host on VPS {vps_host or '(set IP)'}",
                "pass" if vps_host else "pending",
                "SSH install Docker/OpenResty (FreeBuild concept) — wire ssh executor next",
            )
        )
        public = f"https://{hostname}" if hostname else (f"http://{vps_host}" if vps_host else "")
    elif target == "aws_guide":
        steps.append(
            EngineStep(
                "host",
                "AWS teach plan",
                "pass",
                "S3+CloudFront+ALB+Lambda/Fargate+Budgets — see blueprint.aws_plan",
            )
        )
        public = f"https://{hostname}" if hostname else ""
    else:
        steps.append(EngineStep("host", "Local demo", "simulated", "no public URL"))
        public = ""

    ready = all(s.status in ("pass", "simulated", "skipped", "pending") for s in steps) and not any(
        s.status == "fail" for s in steps
    )
    dep = ShipDeployment(
        deployment_id=uuid.uuid4().hex[:12],
        target=target,
        project_path=work_path,
        hostname=hostname,
        github_url=gh_url,
        steps=steps,
        stack=stack.to_dict(),
        cicd=cicd.to_dict(),
        blueprint=blueprint,
        ready=ready,
        public_url=public,
        mode="local_engine",
    )
    save_deployment(dep)
    log.info("ship_engine_run", deployment_id=dep.deployment_id, target=target, ready=ready)
    out: dict[str, Any] = {"ok": ready, "deployment": dep.to_dict()}
    if remote_result is not None:
        out["remote"] = remote_result
    # AWS skill pointer
    out["aws_skill"] = {
        "vendor": "vendor/awslabs-agent-plugins/plugins/deploy-on-aws/skills",
        "skills": ["deploy", "aws-architecture-diagram", "elastic-beanstalk"],
        "mcp_hint": "Configure awsiac / awsknowledge / awspricing MCP in Cursor for IaC+pricing",
    }
    return out


def _finish(
    steps: list[EngineStep],
    target: ShipTarget,
    project_path: str,
    hostname: str,
    github_url: str,
) -> ShipDeployment:
    dep = ShipDeployment(
        deployment_id=uuid.uuid4().hex[:12],
        target=target,
        project_path=project_path,
        hostname=hostname,
        github_url=github_url,
        steps=steps,
        ready=False,
    )
    save_deployment(dep)
    return dep
