"""Auto-detect project stack for deploy orchestration (no manual type pick)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DetectedStack:
    """Result of scanning a project directory for deployable signals."""

    project_path: str
    primary: str
    confidence: float
    signals: list[str] = field(default_factory=list)
    suggested_build: list[str] = field(default_factory=list)
    suggested_services: list[str] = field(default_factory=list)
    needs_database: bool = False
    needs_mail: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_RULES: tuple[tuple[str, tuple[str, ...], float, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "docker-compose",
        ("docker-compose.yml", "compose.yml", "compose.yaml"),
        0.95,
        ("docker compose build", "docker compose up -d"),
        ("docker", "compose"),
    ),
    (
        "dockerfile",
        ("Dockerfile",),
        0.9,
        ("docker build -t app .", "docker run -d --name app app"),
        ("docker",),
    ),
    (
        "node",
        ("package.json",),
        0.8,
        ("npm ci", "npm run build", "npm start"),
        ("node",),
    ),
    (
        "python",
        ("pyproject.toml", "requirements.txt", "Pipfile"),
        0.8,
        ("uv sync", "uv run uvicorn app:app --host 0.0.0.0 --port 8000"),
        ("python",),
    ),
    (
        "go",
        ("go.mod",),
        0.75,
        ("go build -o app .", "./app"),
        ("go",),
    ),
    (
        "rust",
        ("Cargo.toml",),
        0.75,
        ("cargo build --release", "./target/release/app"),
        ("rust",),
    ),
    (
        "static",
        ("index.html",),
        0.55,
        ("npx serve -s .",),
        ("static-http",),
    ),
)


def detect_project(project_path: str | Path) -> DetectedStack:
    """Infer stack from repo files — OpenShip-style without forcing a manual pick."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        return DetectedStack(
            project_path=str(root),
            primary="unknown",
            confidence=0.0,
            signals=["path_missing_or_not_dir"],
            suggested_build=[],
            suggested_services=[],
        )

    hits: list[DetectedStack] = []
    for primary, files, confidence, build, services in _RULES:
        present = [name for name in files if (root / name).exists()]
        if not present:
            continue
        hits.append(
            DetectedStack(
                project_path=str(root),
                primary=primary,
                confidence=confidence,
                signals=present,
                suggested_build=list(build),
                suggested_services=list(services),
            )
        )

    if not hits:
        return DetectedStack(
            project_path=str(root),
            primary="unknown",
            confidence=0.1,
            signals=["no_known_manifest"],
            suggested_build=[],
            suggested_services=[],
        )

    # Prefer highest confidence; docker-compose beats plain dockerfile when both exist.
    best = sorted(hits, key=lambda h: h.confidence, reverse=True)[0]
    needs_db = any(
        (root / name).exists()
        for name in ("prisma/schema.prisma", "alembic.ini", "drizzle.config.ts")
    )
    needs_mail = False
    for mail_marker in ("mail", "email", "smtp.json"):
        if (root / mail_marker).exists():
            needs_mail = True
            break
    compose = root / "docker-compose.yml"
    if compose.is_file() and "mail" in compose.read_text(encoding="utf-8", errors="ignore").lower():
        needs_mail = True

    return DetectedStack(
        project_path=best.project_path,
        primary=best.primary,
        confidence=best.confidence,
        signals=best.signals,
        suggested_build=best.suggested_build,
        suggested_services=best.suggested_services,
        needs_database=needs_db,
        needs_mail=needs_mail,
    )
