"""Project reader seam — everything detection knows about a project comes
through these four methods.

Port of FreeBuild's `project-reader.ts`. Keeping the seam narrow means the same
detection code can later run against a GitHub tree (via `ship/github_auth.py`)
without any of the stack logic becoming source-aware. Only `LocalReader` (the
filesystem) is implemented in this pass.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Directories never worth walking: dependency trees, build output, VCS, venvs.
IGNORED_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".git",
        ".next",
        ".nuxt",
        ".output",
        ".svelte-kit",
        ".astro",
        ".turbo",
        ".vercel",
        ".venv",
        "venv",
        "env",
        "myenv",
        "virtualenv",
        "site-packages",
        ".idea",
        ".vscode",
        ".cache",
        "__pycache__",
        "build",
        "dist",
        "out",
        "coverage",
        "target",
        "vendor",
    }
)

#: A manifest bigger than this is not a manifest — do not read it into memory.
_MAX_MANIFEST_BYTES = 512 * 1024

#: Bounds for the candidate walk. Deep enough for `apps/<name>` and
#: `services/<name>/<sub>`, cheap enough to stay interactive on a huge repo.
_MAX_TREE_DEPTH = 4
_MAX_TREE_ENTRIES = 20000


@dataclass(frozen=True)
class RepoFile:
    """One entry in a directory listing. `path` is repo-relative POSIX."""

    name: str
    path: str
    is_dir: bool


class LocalReader:
    """Reads a project straight off the local filesystem."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def list_directory(self, relative: str = "") -> list[RepoFile]:
        target = self.root / relative if relative else self.root
        try:
            entries = sorted(os.scandir(target), key=lambda e: e.name.lower())
        except OSError:
            return []
        prefix = f"{relative}/" if relative else ""
        out: list[RepoFile] = []
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            out.append(RepoFile(name=entry.name, path=f"{prefix}{entry.name}", is_dir=is_dir))
        return out

    def read_text(self, relative: str) -> str:
        path = self.root / relative
        try:
            if path.stat().st_size > _MAX_MANIFEST_BYTES:
                return ""
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def read_json(self, relative: str) -> dict[str, Any]:
        raw = self.read_text(relative)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def list_tree(self, max_depth: int = _MAX_TREE_DEPTH) -> list[RepoFile]:
        """Bounded breadth-first walk, skipping dependency/build/venv trees."""
        out: list[RepoFile] = []
        queue: list[tuple[str, int]] = [("", 0)]
        while queue and len(out) < _MAX_TREE_ENTRIES:
            relative, depth = queue.pop(0)
            for entry in self.list_directory(relative):
                out.append(entry)
                if not entry.is_dir or depth + 1 >= max_depth:
                    continue
                if entry.name.startswith(".") or entry.name.lower() in IGNORED_DIRS:
                    continue
                queue.append((entry.path, depth + 1))
        return out
