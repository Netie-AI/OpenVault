"""Where in the tree is the deployable project?

Port of FreeBuild's `project-root-detector.ts`, and the replacement for the old
five-entry path whitelist (`apps/dashboard`, `apps/web`, …) that made detection
depend on a sub-app being conventionally named. Candidates come from a real
scan: workspace manifests at the repo root, `vercel.json` directory hints, and
any directory in the tree carrying a stack root marker.

This module deliberately knows nothing about stack detection — it discovers and
ranks *directories*. `detect.py` snapshots each candidate and picks the winner.
"""

from __future__ import annotations

from dataclasses import dataclass

from openmw.openvault.ship.metadata import extract_cd_targets
from openmw.openvault.ship.reader import IGNORED_DIRS, RepoFile
from openmw.openvault.ship.stacks import STACK_ROOT_MARKERS
from openmw.openvault.ship.workspaces import matches_workspace_pattern, workspace_patterns

#: Files that mark a directory as a project-root candidate: every stack's root
#: markers, plus the Nx per-project marker which belongs to no single stack.
DISCOVERED_ROOT_MARKERS: frozenset[str] = STACK_ROOT_MARKERS | {"project.json"}

APPISH_ROOT_SEGMENTS: frozenset[str] = frozenset(
    {"app", "apps", "frontend", "front", "web", "site", "www", "client", "dashboard", "admin"}
)

LIBRARY_ROOT_SEGMENTS: frozenset[str] = frozenset(
    {"package", "packages", "lib", "libs", "shared", "common", "core", "utils", "components"}
)

MAX_PROJECT_ROOT_HINTS = 24

_SOURCE_PRIORITY: dict[str, int] = {"vercel": 4, "workspace": 3, "discovered": 2, "root": 1}

# ─── Ranking weights — the one place to tune candidate selection ─────────────
# Higher wins. A vercel.json hint is an explicit human declaration and beats a
# workspace member, which beats a directory we merely stumbled on.
SOURCE_WEIGHTS: dict[str, int] = {"vercel": 100, "workspace": 60, "discovered": 20, "root": 0}
CATEGORY_WEIGHTS: dict[str, int] = {
    "fullstack": 30,
    "frontend": 20,
    "static": 10,
    "backend": 0,
    "generic": 0,
}
FILE_WEIGHTS: dict[str, int] = {"index.html": 6, "public": 4, "src": 2}
SERVICES_PROJECT_WEIGHT = 16
HAS_BUILD_SCRIPT_WEIGHT = 5
FIRST_IS_APPS_WEIGHT = 10
LAST_IS_APPISH_WEIGHT = 8
FIRST_IS_LIBRARY_WEIGHT = -8
SHALLOW_BONUS_WEIGHT = 1


@dataclass(frozen=True)
class ProjectRootHint:
    root_directory: str
    source: str


def normalize_root_directory(value: str | None) -> str:
    """`./apps/web/` → `apps/web`; `.` and `""` mean the repo root."""
    if not value:
        return ""
    normalized = value.strip().replace("\\", "/").strip("/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized == ".":
        return ""
    return "/".join(part for part in normalized.split("/") if part)


def is_ignored_repo_path(value: str) -> bool:
    normalized = normalize_root_directory(value)
    if not normalized:
        return False
    return any(segment.lower() in IGNORED_DIRS for segment in normalized.split("/"))


def parse_vercel_root_directories(vercel_json: str) -> list[str]:
    """Sub-directories a root `vercel.json` points its build at."""
    from openmw.openvault.ship.metadata import _parse_json_object

    cfg = _parse_json_object(vercel_json) if vercel_json else {}
    if not cfg:
        return []

    directories: list[str] = []

    def add(value: str) -> None:
        candidate = normalize_root_directory(value)
        if not candidate or ".." in candidate.split("/") or is_ignored_repo_path(candidate):
            return
        if candidate not in directories:
            directories.append(candidate)

    build_command = cfg.get("buildCommand")
    if isinstance(build_command, str):
        for target in extract_cd_targets(build_command):
            add(target)

    output = cfg.get("outputDirectory")
    if isinstance(output, str) and "/" in output.strip("/"):
        add(output.strip("/").rsplit("/", 1)[0])

    return directories


def _pre_score(hint: ProjectRootHint) -> int:
    segments = hint.root_directory.split("/") if hint.root_directory else []
    score = _SOURCE_PRIORITY.get(hint.source, 0) * 100
    if not segments:
        return score
    if segments[0].lower() == "apps":
        score += 20
    if segments[-1].lower() in APPISH_ROOT_SEGMENTS:
        score += 12
    if segments[0].lower() in LIBRARY_ROOT_SEGMENTS:
        score -= 12
    return score - len(segments)


def discover_project_root_hints(
    tree: list[RepoFile], root_file_contents: dict[str, str]
) -> list[ProjectRootHint]:
    """Candidate project directories under the repo root, best first."""
    patterns = workspace_patterns(root_file_contents)
    hints: dict[str, ProjectRootHint] = {}

    def record(root_directory: str, source: str) -> None:
        existing = hints.get(root_directory)
        if existing is None or _SOURCE_PRIORITY[source] > _SOURCE_PRIORITY[existing.source]:
            hints[root_directory] = ProjectRootHint(root_directory, source)

    for directory in parse_vercel_root_directories(root_file_contents.get("vercel.json", "")):
        record(directory, "vercel")

    for entry in tree:
        if entry.is_dir or entry.name.lower() not in DISCOVERED_ROOT_MARKERS:
            continue
        parent = entry.path.rsplit("/", 1)[0] if "/" in entry.path else ""
        root_directory = normalize_root_directory(parent)
        if not root_directory or is_ignored_repo_path(root_directory):
            continue
        source = (
            "workspace"
            if any(matches_workspace_pattern(root_directory, p) for p in patterns)
            else "discovered"
        )
        record(root_directory, source)

    # Concrete (non-glob) workspace patterns ARE project roots — a .sln or
    # go.work names them outright, and their marker filenames vary per repo.
    for pattern in patterns:
        if any(ch in pattern for ch in "*?[]"):
            continue
        root_directory = normalize_root_directory(pattern)
        if root_directory and not is_ignored_repo_path(root_directory):
            record(root_directory, "workspace")

    ordered = sorted(hints.values(), key=_pre_score, reverse=True)
    preferred = [hint for hint in ordered if hint.source != "discovered"]
    discovered = [hint for hint in ordered if hint.source == "discovered"]
    if len(preferred) >= MAX_PROJECT_ROOT_HINTS:
        return preferred[:MAX_PROJECT_ROOT_HINTS]
    return preferred + discovered[: MAX_PROJECT_ROOT_HINTS - len(preferred)]


def score_candidate(
    *,
    root_directory: str,
    source: str,
    category: str,
    project_type: str,
    has_build_script: bool,
    file_names: set[str],
) -> int:
    """Rank one candidate sub-project. Higher wins; ties keep discovery order."""
    score = SOURCE_WEIGHTS.get(source, 0)
    score += CATEGORY_WEIGHTS.get(category, 0)
    if project_type == "services":
        score += SERVICES_PROJECT_WEIGHT
    if has_build_script:
        score += HAS_BUILD_SCRIPT_WEIGHT
    for name, weight in FILE_WEIGHTS.items():
        if name in file_names:
            score += weight

    segments = root_directory.split("/") if root_directory else []
    if segments:
        if segments[0].lower() == "apps":
            score += FIRST_IS_APPS_WEIGHT
        if segments[-1].lower() in APPISH_ROOT_SEGMENTS:
            score += LAST_IS_APPISH_WEIGHT
        if segments[0].lower() in LIBRARY_ROOT_SEGMENTS:
            score += FIRST_IS_LIBRARY_WEIGHT
        if len(segments) == 1:
            score += SHALLOW_BONUS_WEIGHT
    return score
