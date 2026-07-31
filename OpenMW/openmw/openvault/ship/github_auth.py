"""GitHub connect — stolen from FreeBuild local-source (gh CLI → PAT → env).

OpenVault owns the token in ``OPENVAULT_HOME/github/`` (never log secrets).
Highest practical scopes via ``gh``: repo, read:org, workflow (matches FreeBuild CLI path).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog

from openmw.openvault.paths import ensure_home

log = structlog.get_logger()

AuthMode = Literal["gh_cli", "pat", "env", "disconnected"]


@dataclass
class GitHubConnection:
    connected: bool
    mode: AuthMode
    login: str | None = None
    scopes: list[str] = field(default_factory=list)
    detail: str = ""
    repos_url: str = "https://api.github.com/user/repos"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GitHubRepo:
    full_name: str
    html_url: str
    clone_url: str
    private: bool
    default_branch: str
    description: str = ""
    language: str | None = None
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_REPO_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com[/:](?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def _github_dir() -> Path:
    path = ensure_home() / "github"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pat_path() -> Path:
    return _github_dir() / "pat.json"


def parse_github_url(url: str) -> tuple[str, str] | None:
    raw = (url or "").strip()
    if not raw:
        return None
    m = _REPO_URL_RE.match(raw)
    if not m:
        return None
    return m.group("owner"), m.group("repo").removesuffix(".git")


def _run(cmd: list[str], *, timeout: float = 30.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, out


def _token_from_gh() -> str | None:
    which = shutil.which("gh")
    if not which:
        return None
    code, out = _run([which, "auth", "token"], timeout=15.0)
    if code != 0:
        return None
    token = out.strip().splitlines()[-1].strip() if out else ""
    return token or None


def _token_from_env() -> str | None:
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "OPENVAULT_GITHUB_TOKEN"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return None


def _token_from_pat_file() -> str | None:
    path = _pat_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    token = str(raw.get("token", "")).strip()
    return token or None


def resolve_token() -> tuple[str | None, AuthMode]:
    gh = _token_from_gh()
    if gh:
        return gh, "gh_cli"
    pat = _token_from_pat_file()
    if pat:
        return pat, "pat"
    env = _token_from_env()
    if env:
        return env, "env"
    return None, "disconnected"


def save_pat(token: str, *, note: str = "") -> GitHubConnection:
    """Store a classic/fine-grained PAT (operator pasted after GitHub register)."""
    cleaned = token.strip()
    if not cleaned:
        raise ValueError("empty token")
    payload = {
        "token": cleaned,
        "note": note,
        "saved_at": time.time(),
        "hint": "Create at https://github.com/settings/tokens with repo + workflow + read:org",
    }
    _pat_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return connection_status()


def clear_pat() -> None:
    path = _pat_path()
    if path.is_file():
        path.unlink()


def _api_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "OpenVault-Ship",
    }


def connection_status() -> GitHubConnection:
    token, mode = resolve_token()
    if not token:
        return GitHubConnection(
            connected=False,
            mode="disconnected",
            detail=(
                "Not connected. Run `gh auth login -s repo,read:org,workflow` "
                "or paste a PAT (repo + workflow + read:org)."
            ),
        )
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get("https://api.github.com/user", headers=_api_headers(token))
            scopes_hdr = resp.headers.get("x-oauth-scopes", "")
            scopes = [s.strip() for s in scopes_hdr.split(",") if s.strip()]
            if resp.status_code >= 400:
                return GitHubConnection(
                    connected=False,
                    mode=mode,
                    detail=f"GitHub API {resp.status_code}: {(resp.text or '')[:200]}",
                    scopes=scopes,
                )
            data = resp.json()
            login = str(data.get("login") or "")
            return GitHubConnection(
                connected=True,
                mode=mode,
                login=login,
                scopes=scopes,
                detail=f"Connected as {login} via {mode}",
            )
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return GitHubConnection(connected=False, mode=mode, detail=str(exc))


def start_gh_login(*, scopes: str = "repo,read:org,workflow") -> dict[str, Any]:
    """Kick ``gh auth login`` web flow (highest useful scopes for ship)."""
    which = shutil.which("gh")
    if not which:
        return {
            "ok": False,
            "error": "gh CLI not found — install GitHub CLI or paste a PAT",
            "register_url": "https://cli.github.com/",
            "pat_url": "https://github.com/settings/tokens?type=beta",
        }
    # Non-interactive hint: operator runs the printed command in a terminal.
    cmd = f'{which} auth login -h github.com -p https -w -s "{scopes}"'
    return {
        "ok": True,
        "mode": "gh_cli",
        "command": cmd,
        "scopes": [s.strip() for s in scopes.split(",") if s.strip()],
        "detail": "Run the command in a terminal; browser opens for highest ship scopes.",
        "after": "Then GET /api/ship/github/status — should show connected.",
    }


def list_repos(*, limit: int = 40, affiliation: str = "owner,collaborator,organization_member") -> dict[str, Any]:
    """Library view — FreeBuild ``/github/home`` equivalent (repos for picker)."""
    status = connection_status()
    if not status.connected:
        return {"ok": False, "connection": status.to_dict(), "repos": []}
    token, _mode = resolve_token()
    assert token is not None
    repos: list[GitHubRepo] = []
    try:
        with httpx.Client(timeout=45.0) as client:
            page = 1
            while len(repos) < limit and page <= 3:
                resp = client.get(
                    "https://api.github.com/user/repos",
                    headers=_api_headers(token),
                    params={
                        "per_page": min(100, limit),
                        "sort": "updated",
                        "affiliation": affiliation,
                        "page": page,
                    },
                )
                if resp.status_code >= 400:
                    return {
                        "ok": False,
                        "connection": status.to_dict(),
                        "repos": [],
                        "error": f"HTTP {resp.status_code}",
                    }
                batch = resp.json()
                if not isinstance(batch, list) or not batch:
                    break
                for item in batch:
                    if not isinstance(item, dict):
                        continue
                    repos.append(
                        GitHubRepo(
                            full_name=str(item.get("full_name") or ""),
                            html_url=str(item.get("html_url") or ""),
                            clone_url=str(item.get("clone_url") or ""),
                            private=bool(item.get("private")),
                            default_branch=str(item.get("default_branch") or "main"),
                            description=str(item.get("description") or ""),
                            language=item.get("language"),
                            updated_at=str(item.get("updated_at") or ""),
                        )
                    )
                    if len(repos) >= limit:
                        break
                page += 1
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"ok": False, "connection": status.to_dict(), "repos": [], "error": str(exc)}
    return {
        "ok": True,
        "connection": status.to_dict(),
        "repos": [r.to_dict() for r in repos[:limit]],
    }


def list_branches(owner: str, repo: str) -> dict[str, Any]:
    status = connection_status()
    if not status.connected:
        return {"ok": False, "branches": [], "connection": status.to_dict()}
    token, _ = resolve_token()
    assert token is not None
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"https://api.github.com/repos/{owner}/{repo}/branches",
                headers=_api_headers(token),
                params={"per_page": 50},
            )
            if resp.status_code >= 400:
                return {"ok": False, "branches": [], "error": f"HTTP {resp.status_code}"}
            data = resp.json()
            names = [str(b.get("name")) for b in data if isinstance(b, dict)]
            return {"ok": True, "branches": names, "connection": status.to_dict()}
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"ok": False, "branches": [], "error": str(exc)}
