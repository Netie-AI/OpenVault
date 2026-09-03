"""Detect existing CI/CD and suggest a minimal GitHub Actions ship workflow.

FreeBuild owns deploy execution; CI should call OpenVault gates then FreeBuild —
not invent a second shipper.

For the OpenVault-owned HTTP path (Caddy + systemd on a VPS / Hetzner / AWS),
`cicd_plan` emits a workflow that builds on the runner, syncs to the box,
restarts the unit, reloads Caddy and curls `/healthz`. A leftover `vercel.json`
is reported as a detect hint only — it never becomes a deploy target.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openmw.openvault.ship.detect import DetectedStack, detect_project
from openmw.openvault.ship.server import service_name_for

WORKFLOW_NAME = "openvault-ship.yml"

_CI_MARKERS: tuple[tuple[str, str], ...] = (
    (".github/workflows", "github_actions"),
    (".gitlab-ci.yml", "gitlab_ci"),
    ("azure-pipelines.yml", "azure_pipelines"),
    ("Jenkinsfile", "jenkins"),
    (".circleci/config.yml", "circleci"),
    ("bitbucket-pipelines.yml", "bitbucket"),
)


@dataclass
class CicdReport:
    """What CI exists + a suggested workflow body (not written unless asked)."""

    project_path: str
    detected: list[str] = field(default_factory=list)
    workflow_paths: list[str] = field(default_factory=list)
    status: str = "missing"  # present | missing | partial
    suggested_workflow_path: str = f".github/workflows/{WORKFLOW_NAME}"
    suggested_workflow: str = ""
    notes: list[str] = field(default_factory=list)
    # OpenVault HTTP ship (Caddy + systemd) view of the same scan.
    github_actions: bool = False
    workflows: list[str] = field(default_factory=list)
    vercel: bool = False
    dockerfile: bool = False
    compose: bool = False
    #: Cursor Origin never fronts HTTP through Vercel — always false.
    origin_vercel_ready: bool = False
    openvault_ship: bool = False
    vercel_ignored: bool = False
    workflow: str = ""
    deploy_target: str = "vps"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_cicd(project_path: str | Path) -> CicdReport:
    """Scan for CI config files under ``project_path``."""
    root = Path(project_path).expanduser().resolve()
    detected: list[str] = []
    paths: list[str] = []
    if not root.is_dir():
        return CicdReport(
            project_path=str(root),
            status="missing",
            notes=["project path missing or not a directory"],
        )

    for rel, kind in _CI_MARKERS:
        candidate = root / rel
        if candidate.is_dir():
            ymls = sorted(candidate.glob("*.yml")) + sorted(candidate.glob("*.yaml"))
            if ymls:
                detected.append(kind)
                paths.extend(str(p.relative_to(root)).replace("\\", "/") for p in ymls)
        elif candidate.is_file():
            detected.append(kind)
            paths.append(rel.replace("\\", "/"))

    workflows_dir = root / ".github" / "workflows"
    workflows: list[str] = []
    if workflows_dir.is_dir():
        workflows = sorted(p.name for p in workflows_dir.glob("*.yml")) + sorted(
            p.name for p in workflows_dir.glob("*.yaml")
        )
    vercel = (root / "vercel.json").is_file()

    status = "present" if detected else "missing"
    notes = [
        "CI should call OpenVault /api/deploy/from-cortex then /execute -- "
        "FreeBuild is SoT for ship.",
        "Do not add a second deploy orchestrator in the workflow.",
        "Suggest-only + simulate-default: a simulated execute must not invent a live host URL.",
    ]
    if status == "present":
        notes.append(f"Found: {', '.join(detected)}. Review before adding OpenVault ship job.")
    if vercel:
        notes.append("vercel.json is a detect hint only; OpenVault serves HTTP itself.")

    suggested = _suggested_github_workflow()
    return CicdReport(
        project_path=str(root),
        detected=detected,
        workflow_paths=paths,
        status=status,
        suggested_workflow=suggested,
        notes=notes,
        github_actions="github_actions" in detected,
        workflows=workflows,
        vercel=vercel,
        dockerfile=(root / "Dockerfile").is_file(),
        compose=any(
            (root / name).is_file()
            for name in ("docker-compose.yml", "compose.yml", "compose.yaml")
        ),
        openvault_ship=WORKFLOW_NAME in workflows,
        vercel_ignored=vercel,
    )


def generate_ship_workflow(
    stack: DetectedStack,
    *,
    hostname: str = "",
    vps_host: str = "",
    provider: str = "vps",
) -> str:
    """GitHub Actions that ship to a VPS. Secrets: VPS_HOST, VPS_SSH_KEY."""
    install = stack.install_command or "true"
    build = stack.build_command or "true"
    host = hostname or "app.example.com"
    ssh_host = vps_host or "${{ secrets.VPS_HOST }}"
    service = service_name_for(stack.project_path)
    remote = f"/var/www/{service}"
    return f"""name: OpenVault ship

# OpenVault ship: build here, run on Hetzner / VPS / AWS behind Caddy.
on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  ship:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install + build ({stack.framework or stack.primary})
        run: |
          {install}
          {build}
      - name: Sync to {provider}
        uses: appleboy/scp-action@v0.1.7
        with:
          host: {ssh_host}
          username: ${{{{ secrets.VPS_USER }}}}
          key: ${{{{ secrets.VPS_SSH_KEY }}}}
          source: "."
          target: "{remote}"
      - name: Reload service + Caddy
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: {ssh_host}
          username: ${{{{ secrets.VPS_USER }}}}
          key: ${{{{ secrets.VPS_SSH_KEY }}}}
          script: |
            sudo systemctl restart {service} || true
            sudo systemctl reload caddy
      - name: Health check load balancer
        run: curl -fsS --retry 8 --retry-delay 2 https://{host}/healthz
"""


def cicd_plan(
    project_path: str,
    *,
    hostname: str = "",
    vps_host: str = "",
    provider: str = "vps",
    write: bool = False,
) -> dict[str, Any]:
    """Detect + OpenVault ship workflow; optionally write it into the repo."""
    stack = detect_project(project_path)
    report = detect_cicd(project_path)
    workflow = generate_ship_workflow(
        stack, hostname=hostname, vps_host=vps_host, provider=provider
    )
    report.workflow = workflow
    report.deploy_target = provider
    written = ""
    if write:
        root = Path(stack.project_path)
        dest = root / ".github" / "workflows" / WORKFLOW_NAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(workflow, encoding="utf-8")
        written = str(dest)
        report.openvault_ship = True
    return {
        **report.to_dict(),
        "stack": stack.to_dict(),
        "written": written,
        "replaces_vercel": True,
    }


def _suggested_github_workflow() -> str:
    return """# OpenVault → FreeBuild (thin CI; custody/gate stay on OpenVault)
name: openvault-ship
on:
  workflow_dispatch:
    inputs:
      subdomain:
        description: Public hostname (app.example.com)
        required: true
      project_path:
        description: Path on the OpenVault host
        required: true
        default: .
      simulate:
        description: Simulate FreeBuild (true/false)
        required: true
        default: "true"
jobs:
  gate-and-ship:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Plan deploy gates
        env:
          OPENVAULT_URL: ${{ secrets.OPENVAULT_URL }}
          OPENVAULT_TOKEN: ${{ secrets.OPENVAULT_TOKEN }}
        run: |
          set -euo pipefail
          curl -fsS -X POST "$OPENVAULT_URL/api/deploy/from-cortex" \\
            -H "Content-Type: application/json" \\
            -H "Authorization: Bearer $OPENVAULT_TOKEN" \\
            -d "{\\"project_path\\":\\"${{ inputs.project_path }}\\",\\
            \\"subdomain\\":\\"${{ inputs.subdomain }}\\",\\
            \\"source\\":\\"cicd\\",\\"intent\\":\\"deploy_to_web\\"}" \\
            | tee plan.json
          python - <<'PY'
          import json, sys
          plan = json.load(open("plan.json", encoding="utf-8"))
          open("deploy_id.txt", "w", encoding="utf-8").write(plan["deploy_id"])
          if not plan.get("ready_to_scale"):
              blocked = [g for g in plan.get("gates", []) if g.get("status") != "pass"]
              print("Blocked gates:", blocked)
              sys.exit(1)
          PY
      - name: Execute FreeBuild
        env:
          OPENVAULT_URL: ${{ secrets.OPENVAULT_URL }}
          OPENVAULT_TOKEN: ${{ secrets.OPENVAULT_TOKEN }}
        run: |
          DEPLOY_ID=$(cat deploy_id.txt)
          curl -fsS -X POST "$OPENVAULT_URL/api/deploy/$DEPLOY_ID/execute" \\
            -H "Content-Type: application/json" \\
            -H "Authorization: Bearer $OPENVAULT_TOKEN" \\
            -d '{"simulate": ${{ inputs.simulate }}}'
"""
