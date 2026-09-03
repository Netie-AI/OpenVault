"""Deployment metadata overlay — `openvault.json` > `vercel.json` > `railway.*` > `render.yaml`.

Python port of FreeBuild's `packages/core/src/metadata/*.ts`. A repo that already
tells a PaaS how to build and run itself should deploy the same way here, so
these declarations fold over heuristic detection: the native and PaaS-authored
files override, `render.yaml` only fills gaps.

The native filename is `openvault.json` (FreeBuild's `openship.json` is still
read, so a repo carrying one keeps working).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openmw.openvault.ship.languages import strip_bom

#: Vercel framework slugs that differ from our stack ids.
_VERCEL_FRAMEWORK_TO_STACK: dict[str, str] = {
    "nextjs": "nextjs",
    "vite": "vite",
    "astro": "astro",
    "nuxtjs": "nuxt",
    "vue": "vue",
    "svelte": "sveltekit",
    "sveltekit": "sveltekit",
    "remix": "remix",
    "gatsby": "gatsby",
    "angular": "angular",
    "create-react-app": "cra",
}

_CD_TARGET = re.compile(r"(?:^|&&|;|\|)\s*cd\s+['\"]?([^'\"&;|\s]+)")


@dataclass(frozen=True)
class DeploymentMetadata:
    source: str
    install_command: str = ""
    build_command: str = ""
    start_command: str = ""
    output_directory: str = ""
    framework: str = ""
    env: dict[str, str] = field(default_factory=dict)
    #: Commands `cd` into a DIFFERENT directory — they describe another
    #: directory's build, so they must not be applied to the one they sit in.
    non_local: bool = False
    #: Weak signal: only fills fields detection left empty.
    fill_only: bool = False

    def has_signal(self) -> bool:
        return bool(
            self.install_command
            or self.build_command
            or self.start_command
            or self.output_directory
            or self.framework
        )


def _trimmed(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def extract_cd_targets(command: str) -> list[str]:
    """Directories a shell command `cd`s into, excluding the current dir."""
    targets = []
    for match in _CD_TARGET.finditer(command or ""):
        target = match.group(1).rstrip("/")
        if target and target != ".":
            targets.append(target)
    return targets


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(strip_bom(raw))
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_native(file_contents: dict[str, str]) -> DeploymentMetadata | None:
    raw = file_contents.get("openvault.json") or file_contents.get("openship.json")
    if not raw:
        return None
    cfg = _parse_json_object(raw)
    if not cfg:
        return None
    meta = DeploymentMetadata(
        source="openvault",
        install_command=_trimmed(cfg.get("installCommand")),
        build_command=_trimmed(cfg.get("buildCommand")),
        start_command=_trimmed(cfg.get("startCommand")),
        output_directory=_trimmed(cfg.get("outputDirectory")),
        framework=_trimmed(cfg.get("framework")),
    )
    return meta if meta.has_signal() else None


def _parse_vercel(file_contents: dict[str, str]) -> DeploymentMetadata | None:
    raw = file_contents.get("vercel.json")
    if not raw:
        return None
    cfg = _parse_json_object(raw)
    if not cfg:
        return None
    build = _trimmed(cfg.get("buildCommand"))
    install = _trimmed(cfg.get("installCommand"))
    framework_slug = _trimmed(cfg.get("framework")).lower()
    meta = DeploymentMetadata(
        source="vercel",
        install_command=install,
        build_command=build,
        output_directory=_trimmed(cfg.get("outputDirectory")),
        framework=_VERCEL_FRAMEWORK_TO_STACK.get(framework_slug, ""),
        non_local=bool(extract_cd_targets(build) or extract_cd_targets(install)),
    )
    return meta if meta.has_signal() else None


def _parse_railway(file_contents: dict[str, str]) -> DeploymentMetadata | None:
    raw_json = file_contents.get("railway.json")
    build = start = builder = ""
    if raw_json:
        cfg = _parse_json_object(raw_json)
        raw_build, raw_deploy = cfg.get("build"), cfg.get("deploy")
        build_block: dict[str, Any] = raw_build if isinstance(raw_build, dict) else {}
        deploy_block: dict[str, Any] = raw_deploy if isinstance(raw_deploy, dict) else {}
        build = _trimmed(build_block.get("buildCommand"))
        builder = _trimmed(build_block.get("builder"))
        start = _trimmed(deploy_block.get("startCommand"))

    raw_toml = file_contents.get("railway.toml")
    if raw_toml and not (build or start or builder):
        section = ""
        for line in raw_toml.splitlines():
            header = re.match(r"^\s*\[([^\]]+)\]", line)
            if header:
                section = header.group(1).strip().lower()
                continue
            kv = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.+)$", line)
            if not kv:
                continue
            key, rest = kv.group(1), kv.group(2).strip()
            table, _, field_name = key.rpartition(".")
            table = table.lower() or section
            quoted = rest[:1] in "\"'" and rest[-1:] == rest[:1]
            value = rest[1:-1] if quoted else rest.split("#")[0].strip()
            if table == "build" and field_name == "buildCommand":
                build = build or value
            elif table == "build" and field_name == "builder":
                builder = builder or value
            elif table == "deploy" and field_name == "startCommand":
                start = start or value

    # A DOCKERFILE builder means "build from the Dockerfile"; NIXPACKS auto-detects.
    framework = "docker" if builder.upper() == "DOCKERFILE" else ""
    meta = DeploymentMetadata(
        source="railway",
        build_command=build,
        start_command=start,
        framework=framework,
        non_local=bool(extract_cd_targets(build)),
    )
    return meta if meta.has_signal() else None


def _parse_render(file_contents: dict[str, str]) -> DeploymentMetadata | None:
    raw = file_contents.get("render.yaml")
    if not raw:
        return None
    start = build = ""
    for line in raw.splitlines():
        start_match = re.match(r"^\s*startCommand:\s*(.+)$", line)
        if start_match and not start:
            start = _unquote(start_match.group(1))
        build_match = re.match(r"^\s*buildCommand:\s*(.+)$", line)
        if build_match and not build:
            build = _unquote(build_match.group(1))
    # render.yaml's buildCommand conflates install+build for many stacks, so a
    # bare install is noise, not a build.
    if re.match(r"^(npm|yarn|pnpm|bun)\s+(install|i|ci)\b", build):
        build = ""
    meta = DeploymentMetadata(
        source="render", build_command=build, start_command=start, fill_only=True
    )
    return meta if meta.has_signal() else None


def _unquote(value: str) -> str:
    trimmed = value.strip()
    match = re.match(r"^(['\"])(.*)\1$", trimmed)
    return match.group(2) if match else trimmed


#: Parsers in PRECEDENCE order (highest first).
_PARSERS: tuple[Callable[[dict[str, str]], DeploymentMetadata | None], ...] = (
    _parse_native,
    _parse_vercel,
    _parse_railway,
    _parse_render,
)

METADATA_FILES: tuple[str, ...] = (
    "openvault.json",
    "openship.json",
    "vercel.json",
    "railway.json",
    "railway.toml",
    "render.yaml",
)


def parse_deployment_metadata(file_contents: dict[str, str]) -> list[DeploymentMetadata]:
    """Every metadata declaration found in one directory, in precedence order."""
    return [meta for parser in _PARSERS if (meta := parser(file_contents)) is not None]
