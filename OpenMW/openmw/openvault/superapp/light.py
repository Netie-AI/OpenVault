"""Build a light app and hand the artifact to the ship engine.

The engine talks to the real host adapter. This module does not invent a
public URL. A cloud with no vault key fails in the adapter, and the files
are still built on disk.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from openmw.openvault.ship.detect import detect_project
from openmw.openvault.ship.engine import run_ship_engine
from openmw.openvault.superapp.plan import SuperappInputError, plan_superapp

_COPY_SUFFIXES = {".html", ".css", ".js", ".svg", ".txt", ".json", ".ico"}


def build_light_app(project_path: str) -> dict[str, Any]:
    """Copy the light-app files into dist/ and report the detected stack."""
    stack = detect_project(project_path)
    if stack.category not in ("static", "frontend") and stack.primary == "unknown":
        raise SuperappInputError("not a light app: no static or frontend stack")
    root = Path(project_path)
    dist = root / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for path in root.iterdir():
        if path.is_file() and path.suffix.lower() in _COPY_SUFFIXES and path.name != "package.json":
            shutil.copy2(path, dist / path.name)
            copied.append(path.name)
    if not copied:
        raise SuperappInputError(f"nothing to build in {root}")
    return {
        "artifact": str(dist),
        "files": copied,
        "framework": stack.framework,
        "category": stack.category,
        "output_directory": stack.output_directory or ".",
    }


def ship_light_app(
    project_path: str,
    *,
    text: str = "ship this small landing",
    keyed_providers: set[str] | None = None,
    ship_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build, then run the ship engine at the auto-detected cloud."""
    plan = plan_superapp(
        text,
        project_path=project_path,
        keyed_providers=keyed_providers,
    )
    if "ship" not in plan["lanes"]:
        raise SuperappInputError("plan has no ship lane")
    built = build_light_app(project_path)
    cloud = str(plan["recommended_cloud"])
    shipped = run_ship_engine(
        target=cloud,  # type: ignore[arg-type]
        project_path=project_path,
        run_build=True,
        ship_env=ship_env,
    )
    return {"plan": plan, "build": built, "ship": shipped}
