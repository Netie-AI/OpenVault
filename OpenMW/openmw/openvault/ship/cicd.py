"""Detect existing CI/CD and suggest a minimal GitHub Actions ship workflow.

FreeBuild owns deploy execution; CI should call OpenVault gates then FreeBuild —
not invent a second shipper.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
    suggested_workflow_path: str = ".github/workflows/openvault-ship.yml"
    suggested_workflow: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SUGGESTED_WORKFLOW_REL = ".github/workflows/openvault-ship.yml"


def write_suggested_workflow(
    project_path: str | Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write the OpenVault ship workflow onto disk. Never invents a preview URL.

    Vercel git-push publishes because they host the build. We write a workflow the
    operator can ``git push``; GitHub Actions green is not a live host URL. Simulate
    stays the default on dispatch. Loopback OpenVault is unreachable from
    GitHub-hosted runners — that is said in the file, not hidden.
    """
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        return {
            "ok": False,
            "written": False,
            "path": SUGGESTED_WORKFLOW_REL,
            "public_url": None,
            "git_push": "suggest-only",
            "detail": "project path missing or not a directory",
        }
    dest = root / SUGGESTED_WORKFLOW_REL
    suggested = _suggested_github_workflow()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and not overwrite:
        existing = dest.read_text(encoding="utf-8")
        return {
            "ok": True,
            "written": False,
            "already_present": True,
            "same_as_suggested": existing == suggested,
            "path": SUGGESTED_WORKFLOW_REL,
            "public_url": None,
            "git_push": "workflow-on-disk",
            "detail": (
                "Workflow already on disk. Pass overwrite=true to replace. "
                "git push is yours; this write does not invent a live URL."
            ),
        }
    dest.write_text(suggested, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "written": True,
        "already_present": False,
        "path": SUGGESTED_WORKFLOW_REL,
        "public_url": None,
        "git_push": "workflow-on-disk",
        "detail": (
            "Wrote OpenVault ship workflow. Commit and git push from this repo when "
            "you want it on GitHub. Actions will not invent a preview URL (HT1)."
        ),
    }


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

    status = "present" if detected else "missing"
    notes = [
        "CI should call OpenVault /api/deploy/from-cortex then /execute — FreeBuild is SoT for ship.",
        "Do not add a second deploy orchestrator in the workflow.",
        "Suggest-only + simulate-default: a simulated execute must not invent a live host URL.",
    ]
    if status == "present":
        notes.append(f"Found: {', '.join(detected)}. Review before adding OpenVault ship job.")

    suggested = _suggested_github_workflow()
    return CicdReport(
        project_path=str(root),
        detected=detected,
        workflow_paths=paths,
        status=status,
        suggested_workflow_path=SUGGESTED_WORKFLOW_REL,
        suggested_workflow=suggested,
        notes=notes,
    )


def _suggested_github_workflow() -> str:
    return """# OpenVault ship — not Vercel. Green CI is not a live URL.
name: openvault-ship
on:
  push:
    paths:
      - ".github/workflows/openvault-ship.yml"
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
  no-fake-preview:
    runs-on: ubuntu-latest
    steps:
      - name: Refuse to invent a preview URL
        run: |
          echo "OpenVault does not mint *.vercel.app (or any) preview from this job."
          echo "Ship from the laptop (Spaceship FTP / host adapters). HT1 is human."
          echo "GitHub-hosted runners cannot reach 127.0.0.1:5000."
  gate-and-ship:
    if: github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Plan deploy gates
        env:
          OPENVAULT_URL: ${{ secrets.OPENVAULT_URL }}
          OPENVAULT_TOKEN: ${{ secrets.OPENVAULT_TOKEN }}
        run: |
          set -euo pipefail
          if [ -z "${OPENVAULT_URL:-}" ]; then
            echo "OPENVAULT_URL unset — skip remote gate. Use the OpenVault laptop for ship."
            exit 0
          fi
          curl -fsS -X POST "$OPENVAULT_URL/api/deploy/from-cortex" \\
            -H "Content-Type: application/json" \\
            -H "Authorization: Bearer $OPENVAULT_TOKEN" \\
            -d "{\\"project_path\\":\\"${{ inputs.project_path }}\\",\\"subdomain\\":\\"${{ inputs.subdomain }}\\",\\"source\\":\\"cicd\\",\\"intent\\":\\"deploy_to_web\\"}" \\
            | tee plan.json
          python - <<'PY'
          import json, sys
          plan = json.load(open("plan.json", encoding="utf-8"))
          open("deploy_id.txt", "w", encoding="utf-8").write(plan["deploy_id"])
          if not plan.get("ready_to_scale"):
              print("Blocked gates:", [g for g in plan.get("gates", []) if g.get("status") != "pass"])
              sys.exit(1)
          PY
      - name: Execute FreeBuild
        env:
          OPENVAULT_URL: ${{ secrets.OPENVAULT_URL }}
          OPENVAULT_TOKEN: ${{ secrets.OPENVAULT_TOKEN }}
        run: |
          if [ -z "${OPENVAULT_URL:-}" ] || [ ! -f deploy_id.txt ]; then
            echo "No remote OpenVault — nothing executed, no URL invented."
            exit 0
          fi
          DEPLOY_ID=$(cat deploy_id.txt)
          curl -fsS -X POST "$OPENVAULT_URL/api/deploy/$DEPLOY_ID/execute" \\
            -H "Content-Type: application/json" \\
            -H "Authorization: Bearer $OPENVAULT_TOKEN" \\
            -d '{"simulate": ${{ inputs.simulate }}}'
"""
