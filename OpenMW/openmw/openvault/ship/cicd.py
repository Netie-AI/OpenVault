"""CI/CD signal scan — workflows, Vercel, Docker — used by POST /api/deploy/cicd."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CicdReport:
    project_path: str
    github_actions: bool = False
    workflows: list[str] = field(default_factory=list)
    vercel: bool = False
    dockerfile: bool = False
    compose: bool = False
    origin_vercel_ready: bool = False

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
        origin_vercel_ready=vercel,
    )
