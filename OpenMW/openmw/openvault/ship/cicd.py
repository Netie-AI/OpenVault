"""CI/CD for OpenVault-shipped apps — build on the box, not on Vercel.

Detects existing GitHub Actions / Origin workflows, flags leftover vercel.json
as unused, and emits an OpenVault ship workflow: build by stack type, rsync to
Hetzner/VPS/AWS, reload Caddy, health-check the load balancer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openmw.openvault.ship.detect import DetectedStack, detect_project
from openmw.openvault.ship.server import service_name_for

WORKFLOW_NAME = "openvault-ship.yml"


@dataclass
class CicdReport:
    project_path: str
    github_actions: bool = False
    workflows: list[str] = field(default_factory=list)
    vercel: bool = False
    dockerfile: bool = False
    compose: bool = False
    origin_vercel_ready: bool = False
    openvault_ship: bool = False
    vercel_ignored: bool = False
    workflow: str = ""
    deploy_target: str = "vps"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_cicd(project_path: str) -> CicdReport:
    root = Path(project_path).expanduser()
    workflows_dir = root / ".github" / "workflows"
    workflows: list[str] = []
    if workflows_dir.is_dir():
        workflows = sorted(p.name for p in workflows_dir.glob("*.yml")) + sorted(
            p.name for p in workflows_dir.glob("*.yaml")
        )
    vercel = (root / "vercel.json").is_file()
    has_ours = WORKFLOW_NAME in workflows
    return CicdReport(
        project_path=str(root),
        github_actions=bool(workflows),
        workflows=workflows,
        vercel=vercel,
        dockerfile=(root / "Dockerfile").is_file(),
        compose=any(
            (root / name).is_file()
            for name in ("docker-compose.yml", "compose.yml", "compose.yaml")
        ),
        origin_vercel_ready=False,
        openvault_ship=has_ours,
        vercel_ignored=vercel,
        workflow="",
        deploy_target="vps",
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
