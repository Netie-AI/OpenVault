"""Cursor Origin git-host adapter (not an app runtime).

Origin stores repositories at https://origin.cursor.com/{owner}/{repo}.git and
https://cursor.com/codebase/{owner}/{repo}. HTTP is OpenVault: Caddy + systemd
on Hetzner / VPS / AWS. Origin does not run apps and has no `origin deploy`.

Docs: https://cursor.com/docs/origin
CLI: `origin` (https://cursor.com/docs/origin/cli)
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

StepStatus = Literal["pass", "fail", "pending", "skipped", "simulated"]


@dataclass
class OriginStep:
    id: str
    title: str
    status: StepStatus
    detail: str = ""
    command: str | None = None


@dataclass
class OriginPlan:
    plan_id: str
    project_path: str
    hostname: str
    owner: str
    repo: str
    remote_url: str
    browse_url: str
    vercel_http: bool
    openvault_http: bool
    stack: dict[str, Any]
    steps: list[OriginStep] = field(default_factory=list)
    ready: bool = False
    executed: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "project_path": self.project_path,
            "hostname": self.hostname,
            "owner": self.owner,
            "repo": self.repo,
            "remote_url": self.remote_url,
            "browse_url": self.browse_url,
            "vercel_http": self.vercel_http,
            "openvault_http": self.openvault_http,
            "stack": self.stack,
            "steps": [asdict(s) for s in self.steps],
            "ready": self.ready,
            "executed": self.executed,
            "created_at": self.created_at,
        }


def origin_status() -> dict[str, Any]:
    cli = os.environ.get("ORIGIN_CLI", "origin")
    which = shutil.which(cli)
    mode = os.environ.get("ORIGIN_MODE", "auto")
    owner = os.environ.get("ORIGIN_OWNER") or os.environ.get("CURSOR_ORIGIN_OWNER") or ""
    api = os.environ.get("ORIGIN_API_URL", "https://api.cursor.com/v1/origin").rstrip("/")
    if mode == "auto":
        mode = "cli" if which is not None else "simulate"
    ready = mode == "simulate" or which is not None or bool(os.environ.get("ORIGIN_TOKEN"))
    detail = f"mode={mode} cli={which or 'missing'} owner={owner or '(set ORIGIN_OWNER)'}"
    return {
        "cli_configured": cli,
        "cli_found": which is not None,
        "cli_path": which,
        "mode": mode,
        "ready": ready,
        "owner": owner,
        "api_url": api,
        "git_host": "https://origin.cursor.com",
        "browse_host": "https://cursor.com/codebase",
        "detail": detail,
        "notes": (
            "Origin hosts git, not running apps. HTTP is OpenVault Caddy + systemd "
            "on Hetzner / VPS / AWS (not Vercel)."
        ),
    }


def _plans_dir() -> Path:
    path = ensure_home() / "origin_plans"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _repo_name(project_path: str, override: str = "") -> str:
    if override.strip():
        return override.strip()
    return Path(project_path).resolve().name or "app"


def build_origin_plan(
    *,
    project_path: str,
    hostname: str = "",
    owner: str = "",
    repo: str = "",
    stack: DetectedStack | None = None,
) -> OriginPlan:
    detected = stack or detect_project(project_path)
    status = origin_status()
    owner_s = owner or str(status.get("owner") or "owner")
    repo_s = _repo_name(detected.project_path or project_path, repo)
    remote = f"https://origin.cursor.com/{owner_s}/{repo_s}.git"
    browse = f"https://cursor.com/codebase/{owner_s}/{repo_s}"
    steps: list[OriginStep] = []

    if detected.primary == "unknown" or detected.confidence < 0.5:
        steps.append(OriginStep("detect", "Detect app type", "fail", "unknown stack"))
    else:
        steps.append(
            OriginStep(
                "detect",
                "Detect app type",
                "pass",
                f"{detected.framework or detected.primary} kind={detected.host_kind}",
            )
        )

    steps.append(
        OriginStep(
            "origin_auth",
            "Origin CLI / token",
            "pass" if status["ready"] else "pending",
            status["detail"],
            command="origin auth login",
        )
    )
    steps.append(
        OriginStep(
            "origin_repo",
            "Create Origin repo",
            "pass" if status["ready"] else "pending",
            f"{owner_s}/{repo_s}",
            command=f"origin repo create {repo_s}",
        )
    )
    steps.append(
        OriginStep(
            "git_remote",
            "Point git remote at Origin",
            "pass",
            remote,
            command=f"git remote add origin {remote}",
        )
    )
    steps.append(
        OriginStep(
            "git_push",
            "Push branch to Origin",
            "pass" if status["ready"] else "pending",
            "git push -u origin HEAD",
            command="git push -u origin HEAD",
        )
    )
    steps.append(
        OriginStep(
            "http_runtime",
            "OpenVault HTTP (replaces Vercel)",
            "pass",
            (
                f"{detected.host_kind}: Caddy + systemd on Hetzner/VPS/AWS. "
                f"Start: {detected.start_command or 'caddy file_server'}"
            ),
        )
    )
    steps.append(
        OriginStep(
            "http_auto_update",
            "HTTP auto-update",
            "pass",
            "git pull on the VM + systemd restart; Caddy keeps the hostname",
        )
    )

    if hostname and "." in hostname:
        steps.append(
            OriginStep(
                "load_balancer",
                "Hostname / TLS / load balancer",
                "pass",
                f"A/CNAME {hostname} → VPS; Caddy Let's Encrypt on :443",
                command=f"openship dns ensure --host {hostname}",
            )
        )
    else:
        steps.append(
            OriginStep(
                "load_balancer",
                "Hostname / TLS / load balancer",
                "fail",
                "hostname like app.example.com required",
            )
        )

    blockers = [s for s in steps if s.status == "fail"]
    plan = OriginPlan(
        plan_id=uuid.uuid4().hex[:12],
        project_path=str(detected.project_path),
        hostname=hostname,
        owner=owner_s,
        repo=repo_s,
        remote_url=remote,
        browse_url=browse,
        vercel_http=False,
        openvault_http=True,
        stack=detected.to_dict(),
        steps=steps,
        ready=len(blockers) == 0,
    )
    _save(plan)
    return plan


def execute_origin_plan(plan: OriginPlan, *, simulate: bool | None = None) -> OriginPlan:
    status = origin_status()
    force_sim = simulate if simulate is not None else status["mode"] != "cli"
    for step in plan.steps:
        if step.status == "fail":
            continue
        if force_sim or status["mode"] == "simulate":
            step.status = "simulated"
            step.detail = f"simulated: {step.detail}"
            continue
        if status["cli_found"] and step.command and step.id in {"origin_auth", "origin_repo"}:
            ok, detail = _run_checked(step.command.split(), timeout=120.0)
            step.status = "pass" if ok else "fail"
            step.detail = detail[:2000]
        else:
            step.status = "simulated"
            step.detail = f"no live git push from this console — {step.detail}"
    plan.executed = True
    plan.ready = all(s.status in ("pass", "simulated", "skipped") for s in plan.steps)
    _save(plan)
    log.info("origin_execute", plan_id=plan.plan_id, ready=plan.ready, simulated=force_sim)
    return plan


def load_origin_plan(plan_id: str) -> OriginPlan | None:
    path = _plans_dir() / f"{plan_id}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    steps = [OriginStep(**s) for s in raw.get("steps", [])]
    return OriginPlan(
        plan_id=raw["plan_id"],
        project_path=raw["project_path"],
        hostname=raw.get("hostname", ""),
        owner=raw.get("owner", ""),
        repo=raw.get("repo", ""),
        remote_url=raw.get("remote_url", ""),
        browse_url=raw.get("browse_url", ""),
        vercel_http=False,
        openvault_http=bool(raw.get("openvault_http", True)),
        stack=dict(raw.get("stack") or {}),
        steps=steps,
        ready=bool(raw.get("ready")),
        executed=bool(raw.get("executed")),
        created_at=float(raw.get("created_at", time.time())),
    )


def _save(plan: OriginPlan) -> Path:
    path = _plans_dir() / f"{plan.plan_id}.json"
    path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    return path


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
