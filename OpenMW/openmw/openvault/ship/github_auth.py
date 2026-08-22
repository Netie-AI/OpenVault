"""Optional GitHub PAT / `gh` status for ship library import. Never logs the token."""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openmw.openvault.paths import ensure_home


@dataclass
class GithubStatus:
    gh_cli: bool
    pat_stored: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pat_path() -> Path:
    return ensure_home() / "github_pat"


def connection_status() -> GithubStatus:
    stored = _pat_path().is_file()
    return GithubStatus(
        gh_cli=shutil.which("gh") is not None,
        pat_stored=stored,
        note="PAT stored locally" if stored else "no PAT",
    )


def start_gh_login() -> dict[str, Any]:
    return {
        "ok": False,
        "detail": "Run `gh auth login` in a terminal; this console does not spawn a browser login.",
        "status": connection_status().to_dict(),
    }


def save_pat(token: str, note: str = "") -> GithubStatus:
    path = _pat_path()
    path.write_text(token.strip(), encoding="utf-8")
    os.chmod(path, 0o600)
    status = connection_status()
    status.note = note or status.note
    return status


def clear_pat() -> None:
    path = _pat_path()
    if path.is_file():
        path.unlink()


def list_repos() -> dict[str, Any]:
    return {"repos": [], "detail": "Connect gh or a PAT to list GitHub repos."}


def list_branches(owner: str, repo: str) -> dict[str, Any]:
    return {"owner": owner, "repo": repo, "branches": []}
