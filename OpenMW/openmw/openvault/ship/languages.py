"""Manifest → dependency parsers, one per language family.

Python port of FreeBuild's `packages/core/src/languages/*.ts`. Every parser
returns a `{dependency-name: version}` map so stack detection matches on real
declared dependencies instead of grepping the manifest text — a pyproject whose
*description* mentions Django is not a Django project.

Where a real parser exists in the stdlib we use it (`tomllib`, `json`); the
line-oriented formats (go.mod, Gemfile, mix.exs) keep the vendor's regexes
because there is no stdlib parser for them.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    # tomli IS tomllib (same author, same API) backported; pyproject declares it
    # for python_version < "3.11". Imported unconditionally on purpose: a missing
    # TOML parser must fail loudly at import, not turn every pyproject.toml /
    # Cargo.toml / Pipfile into "no dependencies found" at runtime.
    import tomli as tomllib


def strip_bom(content: str) -> str:
    return content.lstrip("﻿")


def _normalize_python_name(raw: str) -> str:
    """PEP 503-ish: lowercase, '-' and '.' collapse to '_' so dialects match."""
    return re.sub(r"[-.]+", "_", raw.strip().lower())


# ─── JavaScript / TypeScript ─────────────────────────────────────────────────


def parse_package_json(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(strip_bom(content))
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _js_deps(content: str) -> dict[str, str]:
    pkg = parse_package_json(content)
    deps: dict[str, str] = {}
    for key in ("dependencies", "devDependencies"):
        block = pkg.get(key)
        if isinstance(block, dict):
            deps.update({str(k): str(v) for k, v in block.items()})
    return deps


# ─── Python ──────────────────────────────────────────────────────────────────


def parse_pyproject(content: str) -> dict[str, Any]:
    """Parsed pyproject.toml, or {} when it is missing/invalid."""
    try:
        parsed = tomllib.loads(strip_bom(content))
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _requirement_name(spec: str) -> str:
    """`fastapi[standard]>=0.1  # comment` → `fastapi`."""
    head = spec.split("#", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", head)
    return _normalize_python_name(match.group(1)) if match else ""


def _requirements_deps(content: str) -> dict[str, str]:
    deps: dict[str, str] = {}
    for raw in strip_bom(content).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = _requirement_name(line)
        if name:
            deps[name] = line[len(name) :] or "*"
    return deps


def _pyproject_deps(content: str) -> dict[str, str]:
    data = parse_pyproject(content)
    deps: dict[str, str] = {}

    project = data.get("project")
    if isinstance(project, dict):
        for spec in project.get("dependencies") or []:
            if isinstance(spec, str):
                name = _requirement_name(spec)
                if name:
                    deps[name] = "*"
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in optional.values():
                for spec in group or []:
                    if isinstance(spec, str):
                        name = _requirement_name(spec)
                        if name:
                            deps[name] = "*"

    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            for group_key in ("dependencies", "dev-dependencies"):
                block = poetry.get(group_key)
                if isinstance(block, dict):
                    for name in block:
                        if name != "python":
                            deps[_normalize_python_name(str(name))] = "*"
            groups = poetry.get("group")
            if isinstance(groups, dict):
                for group in groups.values():
                    block = group.get("dependencies") if isinstance(group, dict) else None
                    if isinstance(block, dict):
                        for name in block:
                            if name != "python":
                                deps[_normalize_python_name(str(name))] = "*"

    # PEP 735 dependency-groups (uv's `dev` group lives here).
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for specs in groups.values():
            for spec in specs or []:
                if isinstance(spec, str):
                    name = _requirement_name(spec)
                    if name:
                        deps[name] = "*"

    return deps


def _pipfile_deps(content: str) -> dict[str, str]:
    try:
        data = tomllib.loads(strip_bom(content))
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    deps: dict[str, str] = {}
    for section in ("packages", "dev-packages"):
        block = data.get(section)
        if isinstance(block, dict):
            for name in block:
                deps[_normalize_python_name(str(name))] = "*"
    return deps


# ─── Go / Rust / Ruby / PHP / Elixir ──────────────────────────────────────────


def _go_deps(content: str) -> dict[str, str]:
    """`require (...)` blocks and single-line `require` directives.

    Each module also gets a `/vN`-stripped alias so framework matching by import
    path does not have to know the major-version suffix.
    """
    deps: dict[str, str] = {}

    def add(module: str, version: str) -> None:
        deps[module] = version
        base = re.sub(r"/v\d+$", "", module)
        if base != module:
            deps[base] = version

    for block in re.findall(r"require\s*\(([\s\S]*?)\)", content):
        for line in block.splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("//"):
                continue
            parts = trimmed.split()
            if len(parts) >= 2:
                add(parts[0], parts[1])

    for match in re.finditer(r"^require\s+(\S+)\s+(\S+)", content, re.MULTILINE):
        add(match.group(1), match.group(2))

    return deps


def parse_cargo_toml(content: str) -> dict[str, Any]:
    try:
        parsed = tomllib.loads(strip_bom(content))
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _cargo_deps(content: str) -> dict[str, str]:
    data = parse_cargo_toml(content)
    deps: dict[str, str] = {}
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        block = data.get(section)
        if isinstance(block, dict):
            deps.update({str(name): "*" for name in block})
    workspace = data.get("workspace")
    if isinstance(workspace, dict):
        block = workspace.get("dependencies")
        if isinstance(block, dict):
            deps.update({str(name): "*" for name in block})
    return deps


def _gemfile_deps(content: str) -> dict[str, str]:
    return {m.group(1).lower(): "*" for m in re.finditer(r"gem\s+['\"]([^'\"]+)['\"]", content)}


def _composer_deps(content: str) -> dict[str, str]:
    try:
        parsed = json.loads(strip_bom(content))
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    deps: dict[str, str] = {}
    for key in ("require", "require-dev"):
        block = parsed.get(key)
        if isinstance(block, dict):
            deps.update({str(k): str(v) for k, v in block.items()})
    return deps


def _mix_deps(content: str) -> dict[str, str]:
    return {m.group(1): "*" for m in re.finditer(r"\{:(\w+),", content)}


def _no_deps(content: str) -> dict[str, str]:
    """JVM and Docker manifests carry no parseable dep map — detection for those
    stacks runs off `content_patterns` instead."""
    return {}


#: filename (lowercased basename) → dependency parser.
MANIFEST_PARSERS: dict[str, Callable[[str], dict[str, str]]] = {
    "package.json": _js_deps,
    "requirements.txt": _requirements_deps,
    "pyproject.toml": _pyproject_deps,
    "pipfile": _pipfile_deps,
    "go.mod": _go_deps,
    "cargo.toml": _cargo_deps,
    "gemfile": _gemfile_deps,
    "composer.json": _composer_deps,
    "mix.exs": _mix_deps,
    "pom.xml": _no_deps,
    "build.gradle": _no_deps,
    "build.gradle.kts": _no_deps,
    "application.properties": _no_deps,
    "application.yml": _no_deps,
    "application.yaml": _no_deps,
    "dockerfile": _no_deps,
}

LANGUAGE_MANIFEST_FILES: tuple[str, ...] = tuple(MANIFEST_PARSERS)


def collect_dependencies(file_contents: dict[str, str]) -> dict[str, str]:
    """Merge declared dependencies from every manifest present in one directory."""
    deps: dict[str, str] = {}
    for name, parser in MANIFEST_PARSERS.items():
        content = file_contents.get(name)
        if content:
            deps.update(parser(content))
    return deps


# ─── Port detection ──────────────────────────────────────────────────────────

_SCRIPT_PORT = re.compile(r"(?:--port|--PORT|-p)[\s=](\d{2,5})\b")
_EXPOSE = re.compile(r"^EXPOSE\s+(\d{2,5})", re.MULTILINE)
_SPRING_PROPS_PORT = re.compile(r"^\s*server\.port\s*=\s*(\d{2,5})\s*$", re.MULTILINE)


def _clamp_port(raw: str) -> int | None:
    port = int(raw)
    return port if 0 < port <= 65535 else None


def detect_port(package_json: dict[str, Any], file_contents: dict[str, str]) -> int | None:
    """Recover the port the project actually binds, or None to use the default.

    Scanned in order: package.json scripts (`next start --port 3010`), a
    Dockerfile `EXPOSE`, then Spring-style config.
    """
    scripts = package_json.get("scripts")
    if isinstance(scripts, dict):
        # Production scripts win over dev scripts.
        for key in ("start", "dev", "serve", "preview"):
            script = scripts.get(key)
            if isinstance(script, str):
                match = _SCRIPT_PORT.search(script)
                if match:
                    port = _clamp_port(match.group(1))
                    if port:
                        return port

    dockerfile = file_contents.get("dockerfile")
    if dockerfile:
        match = _EXPOSE.search(dockerfile)
        if match:
            port = _clamp_port(match.group(1))
            if port:
                return port

    props = file_contents.get("application.properties")
    if props:
        match = _SPRING_PROPS_PORT.search(props)
        if match:
            port = _clamp_port(match.group(1))
            if port:
                return port

    for name in ("application.yml", "application.yaml"):
        yml = file_contents.get(name)
        if not yml:
            continue
        match = re.search(r"server:[\s\S]*?\bport:\s*[\"']?(\d{2,5})", yml) or re.search(
            r"^\s*port:\s*[\"']?(\d{2,5})", yml, re.MULTILINE
        )
        if match:
            port = _clamp_port(match.group(1))
            if port:
                return port

    return None
