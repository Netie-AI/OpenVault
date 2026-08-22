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
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
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
    #: live | simulated | planned | blocked | failed. `ready` is a bool that
    #: cannot say *why*, and "blocked" is the case that used to read as success.
    status: str = "planned"
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
            "status": self.status,
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
        # Deployments saved before `status` existed: derive it rather than
        # defaulting to "planned", which would relabel a live deploy.
        status=raw.get("status") or ("live" if raw.get("public_url") else "planned"),
        public_url=raw.get("public_url", ""),
        created_at=float(raw.get("created_at", time.time())),
        mode=raw.get("mode", "local_engine"),
    )


def classify_deployment(steps: list[EngineStep], *, mode: str, public_url: str) -> tuple[str, bool]:
    """Name what happened, and whether it counts as success.

    The old rule was ``all(status in ("pass","simulated","skipped","pending"))``,
    which treats a *pending* step as good enough. That is how a vps_ssh run with
    no server address — whose host step says "no server address yet" — answered
    ``ok: true``. The host step is the one that puts the app on the internet; if
    it did not run, the deploy did not happen, whatever the other steps say.
    """
    if any(s.status == "fail" for s in steps):
        return "failed", False

    host_steps = [s for s in steps if s.id == "host"]
    if any(s.status == "pending" for s in host_steps):
        return "blocked", False

    if mode == "live":
        # Belt and braces: "live" without an observed URL is the exact lie
        # hosts/base.py exists to prevent.
        return ("live", True) if public_url else ("blocked", False)
    if mode == "simulated":
        return "simulated", True
    return "planned", True


class DeployInProgressError(RuntimeError):
    """A second deploy for the same project arrived while one was running.

    Concurrent deploys to one VPS is not a hypothetical: both runs read the
    live colour, both pick the same one, both derive the same CRC32 port block,
    and then they race ``docker rm -f`` on identically named containers. The
    openship primitive worth stealing here is exactly this much — one in flight
    per project — and no more. A generic job runner with a cron registry would
    be the third orchestrator PRODUCT_ROLES forbids.
    """

    def __init__(self, project_key: str, *, since: float) -> None:
        super().__init__(f"a deploy for {project_key} is already running")
        self.project_key = project_key
        self.since = since


_ACTIVE_DEPLOYS: dict[str, float] = {}
_ACTIVE_DEPLOYS_LOCK = threading.Lock()


def active_deploys() -> dict[str, float]:
    """Snapshot of in-flight deploys, newest state. For status surfaces."""
    with _ACTIVE_DEPLOYS_LOCK:
        return dict(_ACTIVE_DEPLOYS)


def remote_project_name(*, hostname: str, project_path: str) -> str:
    """The name the host adapter will actually use on the box.

    One helper, used by both the lock and the adapter, because they disagreed:
    the lock keyed on ``project_path or hostname`` while ``_host_vps_ssh`` keyed
    on ``hostname or basename(path)``. With the precedence inverted, two runs
    could take different lock keys and then collide on the same remote
    directory, port block and container names — the exact race the lock exists
    to stop.
    """
    from openmw.openvault.ship.hosts.vps_ssh import normalize_project_name

    if hostname.strip():
        return normalize_project_name(hostname.strip().split(".")[0])
    return normalize_project_name(Path(project_path).name if project_path else "")


def deploy_key(
    *,
    target: str,
    project_path: str,
    hostname: str,
    vps_host: str = "",
    github_url: str = "",
) -> str:
    """Identity of "the same deploy" — the thing two runs would collide on.

    Keyed on the **remote** identity, because that is what physically collides.
    ``D:\\work\\a\\site`` and ``D:\\work\\b\\site`` are different local folders
    that both deploy to ``/srv/openvault/site`` on the same box, sharing a port
    block and container names — so they must share a lock, even though nothing
    about their local paths matches. Adding the local path to the key would let
    both run and race ``rm -rf`` on the same directory.

    Two hostnames from one checkout (staging vs www) get different remote names
    and therefore correctly do not block each other.

    The local path or ``github_url`` is used only when there is no remote name
    yet — otherwise every pre-clone GitHub deploy keyed to the same empty string
    and the second one got a 409 meant for an unrelated repo.
    """
    remote = remote_project_name(hostname=hostname, project_path=project_path)
    if not remote or remote == "openvault-app":
        # No usable remote identity yet. Fall back to the source so unrelated
        # deploys do not share one lock.
        if project_path.strip():
            try:
                remote = os.path.normcase(os.path.realpath(project_path.strip()))
            except (OSError, ValueError):
                remote = os.path.normcase(project_path.strip())
        elif github_url.strip():
            remote = github_url.strip().lower().rstrip("/")
    return "|".join((target, vps_host.strip().lower(), remote))


@contextmanager
def project_deploy_lock(project_key: str) -> Iterator[None]:
    """Hold the slot for one project. Releases on success *and* on failure."""
    with _ACTIVE_DEPLOYS_LOCK:
        started = _ACTIVE_DEPLOYS.get(project_key)
        if started is not None:
            raise DeployInProgressError(project_key, since=started)
        _ACTIVE_DEPLOYS[project_key] = time.time()
    try:
        yield
    finally:
        with _ACTIVE_DEPLOYS_LOCK:
            _ACTIVE_DEPLOYS.pop(project_key, None)


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


def _observed_remote_url(remote_result: dict[str, Any] | None) -> str:
    """Pull a URL only if the remote payload already carried one. Never invent."""
    if not remote_result:
        return ""
    for key in ("public_url", "url", "publicUrl", "hostname"):
        raw = remote_result.get(key)
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if (
            not value
            or "<" in value
            or value.startswith("http://<")
            or value.startswith("https://<")
        ):
            continue
        if value.startswith("http://") or value.startswith("https://"):
            return value
        if key == "hostname" and "." in value:
            return f"https://{value}"
    return ""


def _openship_cloud_host(
    *,
    prefer_remote: bool,
    remote_result: dict[str, Any] | None,
    steps: list[EngineStep],
) -> tuple[EngineStep, str, str]:
    """FreeBuild Cloud host step — simulate labeled, no fake ``*.opsh.io`` URL.

    Returns ``(step, public_url, mode)``. ``mode`` is ``live`` only when an
    observed remote URL is present; otherwise ``simulated`` (or fail).
    """
    remote_step = next((s for s in steps if s.id == "remote"), None)
    if prefer_remote and remote_step is not None and remote_step.status == "pass":
        observed = _observed_remote_url(remote_result)
        if observed:
            return (
                EngineStep(
                    "host",
                    "Host on FreeBuild Cloud",
                    "pass",
                    f"Live at {observed}",
                ),
                observed,
                "live",
            )
        return (
            EngineStep(
                "host",
                "Host on FreeBuild Cloud",
                "fail",
                "remote FreeBuild reported success but no observed host URL — "
                "refusing to invent *.opsh.io",
            ),
            "",
            "local_engine",
        )
    if prefer_remote and remote_step is not None and remote_step.status == "fail":
        return (
            EngineStep(
                "host",
                "Host on FreeBuild Cloud",
                "fail",
                "remote FreeBuild failed — no live host URL",
            ),
            "",
            "local_engine",
        )
    return (
        EngineStep(
            "host",
            "Host on FreeBuild Cloud",
            "simulated",
            "non-production simulate — no live host URL "
            "(set OPENSHIP_URL+TOKEN + prefer_remote for a real *.opsh.io deploy)",
        ),
        "",
        "simulated",
    )


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


def _host_coolify(
    *,
    work_path: Path,
    hostname: str,
) -> tuple[EngineStep, str]:
    """Trigger redeploy on the user's Coolify instance. Returns (step, public_url).

    Coolify builds from the app's configured source — no local artifact upload.
    """
    from openmw.openvault.ship.hosts.coolify import CoolifyAdapter

    project = hostname.split(".")[0] if hostname else work_path.name
    adapter = CoolifyAdapter(
        base_url=os.environ.get("COOLIFY_URL"),
        api_token=os.environ.get("COOLIFY_TOKEN"),
        app_uuid=os.environ.get("COOLIFY_APP_UUID"),
    )
    # Artifact path unused by Coolify; pass work_path for protocol symmetry.
    result = adapter.deploy(work_path, project=project)
    if not result.ok:
        return EngineStep("host", "Coolify", "fail", result.detail), ""

    detail = result.detail
    if hostname:
        domain = adapter.attach_domain(project=project, hostname=hostname)
        if domain.ok:
            detail = f"{detail} · {domain.detail}"
        else:
            records = "; ".join(
                f"{r['type']} {r['name']} -> {r['value']}" for r in domain.required_records
            )
            detail = f"{detail} · {domain.detail}{f' [{records}]' if records else ''}"

    return EngineStep("host", "Coolify", "pass", detail), result.url


def _host_netlify(
    *,
    work_path: Path,
    stack: Any,
    hostname: str,
    run_build: bool,
) -> tuple[EngineStep, str]:
    """Publish to the user's Netlify account. Returns (step, public_url).

    Same honesty as Cloudflare Pages: needs a built artifact directory. URL
    comes only from Netlify's deploy response — never constructed.
    """
    from openmw.openvault.ship.hosts.netlify import NetlifyAdapter

    if not run_build:
        return (
            EngineStep(
                "host",
                "Netlify",
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
                "Netlify",
                "fail",
                "the detected stack has no output directory, so we do not know "
                "which folder to upload",
            ),
            "",
        )

    root = getattr(stack, "root_directory", "") or ""
    artifact = work_path / root / output_dir
    project = hostname.split(".")[0] if hostname else work_path.name

    adapter = NetlifyAdapter(
        api_token=os.environ.get("NETLIFY_AUTH_TOKEN"),
        site_id=os.environ.get("NETLIFY_SITE_ID"),
    )
    result = adapter.deploy(artifact, project=project)
    if not result.ok:
        return EngineStep("host", "Netlify", "fail", result.detail), ""

    detail = result.detail
    if hostname:
        domain = adapter.attach_domain(project=project, hostname=hostname)
        if domain.ok:
            detail = f"{detail} · {domain.detail}"
        else:
            records = "; ".join(
                f"{r['type']} {r['name']} -> {r['value']}" for r in domain.required_records
            )
            detail = f"{detail} · {domain.detail}{f' [{records}]' if records else ''}"

    return EngineStep("host", "Netlify", "pass", detail), result.url


def _host_vps_ssh(
    *,
    work_path: Path,
    stack: Any,
    hostname: str,
    vps_host: str,
    ship_env: Mapping[str, str] | None = None,
) -> tuple[EngineStep, str]:
    """Run the project on the user's own box. Returns (step, public_url).

    This is the only target where OpenVault does the hosting work itself:
    install Docker + Caddy, build, run replicas, load-balance, TLS. A missing
    server address is ``pending`` (the user has nothing to deploy to yet), not
    ``fail`` — everything after that is a real failure with a real reason.
    """
    from openmw.openvault.ship.hosts.vps_ssh import VpsSshAdapter

    label = f"Your VPS {vps_host}" if vps_host else "Your VPS"
    if not vps_host:
        return (
            EngineStep(
                "host",
                "Your VPS",
                "pending",
                "no server address yet — rent a VPS (Hetzner CX22 and up is enough), "
                "put your SSH key on it, then set it here. No URL until it deploys.",
            ),
            "",
        )

    project = remote_project_name(hostname=hostname, project_path=str(work_path))
    adapter = VpsSshAdapter(
        host=vps_host,
        user=os.environ.get("OPENVAULT_VPS_USER", "root"),
        port=int(os.environ.get("OPENVAULT_VPS_PORT", "22") or 22),
        key_path=os.environ.get("OPENVAULT_VPS_KEY", ""),
        stack=stack,
        replicas=int(os.environ.get("OPENVAULT_VPS_REPLICAS", "2") or 2),
    )
    result = adapter.deploy(
        work_path,
        project=project,
        hostname=hostname,
        env=dict(ship_env or {}),
    )
    if not result.ok:
        return EngineStep("host", label, "fail", result.detail), ""

    detail = result.detail
    if hostname:
        domain = adapter.attach_domain(project=project, hostname=hostname)
        if not domain.ok:
            records = "; ".join(
                f"{r['type']} {r['name']} -> {r['value']}" for r in domain.required_records
            )
            detail = f"{detail} · {domain.detail}{f' [{records}]' if records else ''}"

    return EngineStep("host", label, "pass", detail), result.url


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
    ship_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One-stop ship — FreeBuild concept in-process.

    ``prefer_remote_openship`` only if OPENSHIP_URL+TOKEN set; default is local engine.

    Raises :class:`DeployInProgressError` when the same project is already being
    deployed. ``/api/ship/engine`` is a sync endpoint, so FastAPI runs it in a
    threadpool and two clients really can be inside this function at once.
    """
    with project_deploy_lock(
        deploy_key(
            target=target,
            project_path=project_path,
            hostname=hostname,
            vps_host=vps_host,
            github_url=github_url,
        )
    ):
        return _run_ship_engine_locked(
            target=target,
            project_path=project_path,
            github_url=github_url,
            hostname=hostname,
            vps_host=vps_host,
            cloud_tier=cloud_tier,
            monthly_cap_usd=monthly_cap_usd,
            # Pass the caller's value through; the target-derived decision
            # happens inside, where the detected stack is known.
            run_build=run_build,
            prefer_remote_openship=prefer_remote_openship,
            ship_env=ship_env,
        )


def _run_ship_engine_locked(
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
    ship_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The engine proper. Only ``run_ship_engine`` may call this — it holds the lock."""
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

    # Whether a build has to happen HERE is a property of the target, not of
    # whichever caller happened to build the request. one-press hardcoded
    # run_build=False, so Cloudflare Pages and Netlify could never finish
    # through it: their deploy needs a directory that nothing had produced.
    # An explicit run_build=True still forces a build for targets that do not
    # need one (useful to surface build errors before shipping).
    from openmw.openvault.ship.hosts import needs_local_build

    build_here = run_build or needs_local_build(target)

    # Local build (off unless this target needs an artifact from this machine)
    if build_here and stack.suggested_build:
        steps.append(
            EngineStep("build", "Local build", "running", " -> ".join(stack.suggested_build))
        )
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

    # Target-specific closing step.
    # Honesty rule: never invent a live-looking host URL. Only real adapters
    # (Cloudflare Pages, Coolify, Netlify, or a remote FreeBuild response that
    # already carries an observed URL) may set public_url. Simulate / teach /
    # pending host paths stay labeled non-production and leave public_url empty.
    public = ""
    dep_mode = "local_engine"
    if target == "cloudflare_pages":
        # Requires a real build — nothing to upload otherwise.
        step, public = _host_cloudflare_pages(
            work_path=Path(work_path),
            stack=stack,
            hostname=hostname,
            run_build=build_here,
        )
        steps.append(step)
        if step.status == "pass" and public:
            dep_mode = "live"
        elif step.status == "pass" and not public:
            # Adapter contract: success without an observed URL is a failure.
            steps[-1] = EngineStep(
                "host",
                "Cloudflare Pages",
                "fail",
                "deploy reported success but no observed pages.dev URL — refusing to invent one",
            )
            public = ""
    elif target == "coolify":
        step, public = _host_coolify(
            work_path=Path(work_path),
            hostname=hostname,
        )
        steps.append(step)
        if step.status == "pass" and public:
            dep_mode = "live"
        elif step.status == "pass" and not public:
            steps[-1] = EngineStep(
                "host",
                "Coolify",
                "fail",
                "deploy reported success but no observed Coolify URL — refusing to invent one",
            )
            public = ""
    elif target == "netlify":
        step, public = _host_netlify(
            work_path=Path(work_path),
            stack=stack,
            hostname=hostname,
            run_build=build_here,
        )
        steps.append(step)
        if step.status == "pass" and public:
            dep_mode = "live"
        elif step.status == "pass" and not public:
            steps[-1] = EngineStep(
                "host",
                "Netlify",
                "fail",
                "deploy reported success but no observed Netlify URL — refusing to invent one",
            )
            public = ""
    elif target == "openship_cloud":
        host_step, public, dep_mode = _openship_cloud_host(
            prefer_remote=prefer_remote_openship,
            remote_result=remote_result,
            steps=steps,
        )
        steps.append(host_step)
    elif target == "vps_ssh":
        step, public = _host_vps_ssh(
            work_path=Path(work_path),
            stack=stack,
            hostname=hostname,
            vps_host=vps_host,
            ship_env=ship_env or {},
        )
        steps.append(step)
        if step.status == "pass" and public:
            dep_mode = "live"
        elif step.status == "pass" and not public:
            steps[-1] = EngineStep(
                "host",
                "Your VPS",
                "fail",
                "deploy reported success but no observed URL — refusing to invent one",
            )
            public = ""
        else:
            dep_mode = "guide" if step.status == "pending" else "local_engine"
    elif target == "aws_guide":
        steps.append(
            EngineStep(
                "host",
                "AWS teach plan",
                "pass",
                "S3+CloudFront+ALB+Lambda/Fargate+Budgets — see blueprint.aws_plan "
                "(teach-only; no live host URL)",
            )
        )
        dep_mode = "guide"
    else:
        steps.append(
            EngineStep(
                "host",
                "Local demo",
                "simulated",
                "non-production simulate — no public URL",
            )
        )
        dep_mode = "simulated"

    status_name, ready = classify_deployment(steps, mode=dep_mode, public_url=public)
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
        status=status_name,
        public_url=public,
        mode=dep_mode,
    )
    save_deployment(dep)
    log.info(
        "ship_engine_run",
        deployment_id=dep.deployment_id,
        target=target,
        ready=ready,
        status=status_name,
    )
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
