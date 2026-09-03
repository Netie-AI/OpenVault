"""Workspace (monorepo) manifest detectors.

Python port of FreeBuild's `packages/core/src/workspaces/*.ts`. Each detector
recognises one monorepo family's manifest and parses it into the sub-project
paths / globs it declares. The project-root scanner uses the result to mark a
discovered candidate as workspace-sourced, which outranks a bare discovery.

This is what replaces the old five-entry path whitelist in `detect.py`: an app
at `apps/site` is found because the workspace says `apps/*`, not because the
name happened to be blessed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from openmw.openvault.ship.languages import parse_cargo_toml, parse_pyproject


@dataclass(frozen=True)
class WorkspaceDetector:
    id: str
    label: str
    #: Lowercased basenames that trigger this detector.
    manifest_files: tuple[str, ...]
    parse_sub_projects: Callable[[str], list[str]]
    #: JS families set this so install commands can be rewritten to the repo
    #: root; non-JS build tools resolve workspace context implicitly.
    package_manager: str = ""


def _strip_bom(content: str) -> str:
    return content.lstrip("﻿")


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _parse_pnpm_workspace(content: str) -> list[str]:
    """Read the `packages:` block of pnpm-workspace.yaml without a YAML dep."""
    patterns: list[str] = []
    in_packages = False
    for raw in _strip_bom(content).splitlines():
        line = re.sub(r"#.*$", "", raw).rstrip()
        if not line:
            continue
        if not in_packages:
            if re.match(r"^\s*packages\s*:\s*$", line):
                in_packages = True
            continue
        # Another top-level key — the packages block ended.
        if re.match(r"^[A-Za-z0-9_-]+\s*:", line):
            break
        match = re.match(r"^\s*-\s*['\"]?([^'\"#]+?)['\"]?\s*$", line)
        if match:
            patterns.append(match.group(1).strip())
    return patterns


def _parse_package_json_workspaces(content: str) -> list[str]:
    try:
        parsed = json.loads(_strip_bom(content))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, dict):
        return []
    workspaces = parsed.get("workspaces")
    if isinstance(workspaces, dict):
        return _string_list(workspaces.get("packages"))
    return _string_list(workspaces)


def _parse_turbo_json(content: str) -> list[str]:
    """Turbo 2 delegates package discovery to the package manager, so a
    turbo.json usually declares no paths — but it is still a monorepo signal,
    and Turbo 1 repos do carry an explicit list."""
    try:
        parsed = json.loads(_strip_bom(content))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, dict):
        return []
    return _string_list(parsed.get("workspaces")) or _string_list(parsed.get("packages"))


def _parse_cargo_workspace(content: str) -> list[str]:
    workspace = parse_cargo_toml(content).get("workspace")
    return _string_list(workspace.get("members")) if isinstance(workspace, dict) else []


def _parse_uv_workspace(content: str) -> list[str]:
    tool = parse_pyproject(content).get("tool")
    uv = tool.get("uv") if isinstance(tool, dict) else None
    workspace = uv.get("workspace") if isinstance(uv, dict) else None
    return _string_list(workspace.get("members")) if isinstance(workspace, dict) else []


def _parse_go_work(content: str) -> list[str]:
    patterns: list[str] = []
    for block in re.findall(r"use\s*\(([\s\S]*?)\)", content):
        for line in block.splitlines():
            trimmed = line.strip()
            if trimmed and not trimmed.startswith("//"):
                patterns.append(trimmed)
    for match in re.finditer(r"^use\s+(\S+)", content, re.MULTILINE):
        patterns.append(match.group(1))
    return [p.lstrip("./") for p in patterns if p not in (".", "./")]


def _parse_rush(content: str) -> list[str]:
    try:
        parsed = json.loads(_strip_bom(content))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, dict):
        return []
    projects = parsed.get("projects")
    if not isinstance(projects, list):
        return []
    return [
        str(entry["projectFolder"]).strip()
        for entry in projects
        if isinstance(entry, dict) and isinstance(entry.get("projectFolder"), str)
    ]


def _parse_maven_modules(content: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"<module>([^<]+)</module>", content)]


def _parse_gradle_settings(content: str) -> list[str]:
    """`include ':app', ':services:api'` → `app`, `services/api`."""
    patterns: list[str] = []
    for match in re.finditer(r"include\s*\(?\s*([^\n)]+)", content):
        for raw in re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)):
            patterns.append(raw.lstrip(":").replace(":", "/"))
    return patterns


def _parse_dotnet_solution(content: str) -> list[str]:
    """Project lines in a .sln carry `Dir\\Project.csproj` — we want `Dir`."""
    patterns: list[str] = []
    for match in re.finditer(r'Project\("\{[^}]+\}"\)\s*=\s*"[^"]*",\s*"([^"]+)"', content):
        path = match.group(1).replace("\\", "/")
        if "/" in path:
            patterns.append(path.rsplit("/", 1)[0])
    return patterns


def _parse_elixir_umbrella(content: str) -> list[str]:
    return ["apps/*"] if re.search(r"apps_path:\s*[\"']([^\"']+)[\"']", content) else []


WORKSPACE_DETECTORS: tuple[WorkspaceDetector, ...] = (
    WorkspaceDetector("pnpm", "pnpm", ("pnpm-workspace.yaml",), _parse_pnpm_workspace, "pnpm"),
    WorkspaceDetector(
        "npm-workspaces", "npm workspaces", ("package.json",), _parse_package_json_workspaces, "npm"
    ),
    WorkspaceDetector("turbo", "Turborepo", ("turbo.json",), _parse_turbo_json),
    WorkspaceDetector("rush", "Rush", ("rush.json",), _parse_rush, "pnpm"),
    WorkspaceDetector("cargo", "Cargo", ("cargo.toml",), _parse_cargo_workspace),
    WorkspaceDetector("go-work", "Go workspace", ("go.work",), _parse_go_work),
    WorkspaceDetector("uv", "uv", ("pyproject.toml",), _parse_uv_workspace, "uv"),
    WorkspaceDetector("elixir-umbrella", "Elixir umbrella", ("mix.exs",), _parse_elixir_umbrella),
    WorkspaceDetector("maven", "Maven", ("pom.xml",), _parse_maven_modules),
    WorkspaceDetector(
        "gradle", "Gradle", ("settings.gradle", "settings.gradle.kts"), _parse_gradle_settings
    ),
    WorkspaceDetector("dotnet-sln", ".NET solution", (), _parse_dotnet_solution),
)

WORKSPACE_MANIFEST_FILES: frozenset[str] = frozenset(
    name for detector in WORKSPACE_DETECTORS for name in detector.manifest_files
)

#: JS package managers whose install must run at the repo root of a workspace.
JS_PACKAGE_MANAGERS: frozenset[str] = frozenset({"npm", "pnpm", "yarn", "bun"})


@dataclass(frozen=True)
class MatchedWorkspace:
    detector: WorkspaceDetector
    patterns: tuple[str, ...]


def detect_workspaces(file_contents: dict[str, str]) -> list[MatchedWorkspace]:
    """Every workspace family declared at one directory.

    A polyglot repo can match several (a pnpm workspace next to a Cargo one),
    so all matches are returned, not just the first.
    """
    matches: list[MatchedWorkspace] = []
    for detector in WORKSPACE_DETECTORS:
        names = detector.manifest_files or tuple(
            name for name in file_contents if name.endswith(".sln")
        )
        for name in names:
            content = file_contents.get(name)
            if not content:
                continue
            patterns = [p for p in detector.parse_sub_projects(content) if p]
            if patterns:
                matches.append(MatchedWorkspace(detector, tuple(patterns)))
            break
    return matches


def workspace_patterns(file_contents: dict[str, str]) -> list[str]:
    return [pattern for match in detect_workspaces(file_contents) for pattern in match.patterns]


def workspace_package_manager(file_contents: dict[str, str]) -> str:
    """The JS package manager implied by the root's workspace manifest, if any."""
    for match in detect_workspaces(file_contents):
        if match.detector.package_manager in JS_PACKAGE_MANAGERS:
            return match.detector.package_manager
    return ""


def matches_workspace_pattern(root_directory: str, pattern: str) -> bool:
    """Glob-match a repo-relative directory against a workspace pattern."""
    normalized = root_directory.strip("/")
    parts = [p for p in pattern.strip("/").split("/") if p]
    if not normalized or not parts:
        return False
    regex = "/".join(
        ".+" if part == "**" else "[^/]+" if part == "*" else re.escape(part) for part in parts
    )
    return re.fullmatch(regex, normalized) is not None
