"""GitHub connect — stolen from FreeBuild local-source (gh CLI → PAT → env).

Durable PATs live in the sealed KeyVault (same master-key Seal as other
secrets). Legacy ``OPENVAULT_HOME/github/pat.json`` is migrated once then
removed. Never log the raw token. Resolve order: ``gh`` CLI → sealed PAT → env.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog

from openmw.openvault.paths import ensure_home
from openmw.openvault.vault.crypto import VaultSealedError
from openmw.openvault.vault.store import KeyVault

log = structlog.get_logger()

AuthMode = Literal["gh_cli", "pat", "env", "disconnected"]

# Fixed KeyVault row — disabled so chat/routing never picks the ship PAT.
GITHUB_SHIP_PAT_ID = "github-ship-pat"
_PAT_LABEL = "GitHub ship PAT"

_bound_vault: KeyVault | None = None


def bind_vault(vault: KeyVault | None) -> None:
    """Share the process KeyVault/Seal (create_app). None clears the bind."""
    global _bound_vault
    _bound_vault = vault


def _vault(vault: KeyVault | None = None) -> KeyVault:
    if vault is not None:
        return vault
    if _bound_vault is not None:
        return _bound_vault
    return KeyVault()


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


def _scrub_pat_file() -> None:
    path = _pat_path()
    if path.is_file():
        path.unlink()
        log.info("github_pat_file_removed")


def _store_pat_in_vault(vault: KeyVault, token: str, *, note: str = "") -> None:
    label = _PAT_LABEL if not note.strip() else f"{_PAT_LABEL}: {note.strip()}"[:80]
    existing = vault.get(GITHUB_SHIP_PAT_ID)
    if existing is not None:
        vault.update(GITHUB_SHIP_PAT_ID, secret=token, label=label, enabled=False)
        return
    vault.create(
        label=label,
        provider="custom",
        secret=token,
        role="backup",
        priority=9999,
        enabled=False,
        key_id=GITHUB_SHIP_PAT_ID,
    )


def _migrate_legacy_pat(vault: KeyVault) -> None:
    """One-shot: sealed store gets the token; plaintext pat.json is deleted.

    Skipped while sealed (cannot encrypt). Resolve also refuses to read the
    legacy file, so the side door stays closed until unseal + migrate.
    """
    path = _pat_path()
    if not path.is_file() or vault.seal.is_sealed:
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        _scrub_pat_file()
        return
    if not isinstance(raw, dict):
        _scrub_pat_file()
        return
    token = str(raw.get("token", "")).strip()
    note = str(raw.get("note", "")).strip()
    if token:
        _store_pat_in_vault(vault, token, note=note)
        log.info("github_pat_migrated_to_vault")
    _scrub_pat_file()


def _token_from_vault(vault: KeyVault) -> str | None:
    _migrate_legacy_pat(vault)
    if vault.seal.is_sealed:
        return None
    if vault.get(GITHUB_SHIP_PAT_ID) is None:
        return None
    try:
        return vault.get_secret(GITHUB_SHIP_PAT_ID)
    except VaultSealedError:
        return None


def resolve_token(*, vault: KeyVault | None = None) -> tuple[str | None, AuthMode]:
    gh = _token_from_gh()
    if gh:
        return gh, "gh_cli"
    pat = _token_from_vault(_vault(vault))
    if pat:
        return pat, "pat"
    env = _token_from_env()
    if env:
        return env, "env"
    return None, "disconnected"


def save_pat(token: str, *, note: str = "", vault: KeyVault | None = None) -> GitHubConnection:
    """Store a classic/fine-grained PAT behind the vault Seal."""
    cleaned = token.strip()
    if not cleaned:
        raise ValueError("empty token")
    kv = _vault(vault)
    if kv.seal.is_sealed:
        raise VaultSealedError(
            "vault is sealed; POST /api/vault/unseal with the passphrase first"
        )
    _store_pat_in_vault(kv, cleaned, note=note)
    _scrub_pat_file()
    log.info("github_pat_saved_to_vault")
    return connection_status(vault=kv)


def clear_pat(*, vault: KeyVault | None = None) -> None:
    kv = _vault(vault)
    if kv.seal.is_sealed:
        raise VaultSealedError(
            "vault is sealed; POST /api/vault/unseal with the passphrase first"
        )
    kv.delete(GITHUB_SHIP_PAT_ID)
    _scrub_pat_file()
    log.info("github_pat_cleared")


def _api_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "OpenVault-Ship",
    }


def connection_status(*, vault: KeyVault | None = None) -> GitHubConnection:
    token, mode = resolve_token(vault=vault)
    if not token:
        detail = (
            "Not connected. Run `gh auth login -s repo,read:org,workflow` "
            "or paste a PAT (repo + workflow + read:org)."
        )
        kv = _vault(vault)
        if kv.seal.is_sealed and kv.get(GITHUB_SHIP_PAT_ID) is not None:
            detail = (
                "Vault is sealed; unseal to use the stored GitHub PAT, "
                "or connect via gh CLI / env."
            )
        return GitHubConnection(
            connected=False,
            mode="disconnected",
            detail=detail,
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


def list_repos(
    *,
    limit: int = 40,
    affiliation: str = "owner,collaborator,organization_member",
    vault: KeyVault | None = None,
) -> dict[str, Any]:
    """Library view — FreeBuild ``/github/home`` equivalent (repos for picker)."""
    status = connection_status(vault=vault)
    if not status.connected:
        return {"ok": False, "connection": status.to_dict(), "repos": []}
    token, _mode = resolve_token(vault=vault)
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


def list_branches(
    owner: str, repo: str, *, vault: KeyVault | None = None
) -> dict[str, Any]:
    status = connection_status(vault=vault)
    if not status.connected:
        return {"ok": False, "branches": [], "connection": status.to_dict()}
    token, _ = resolve_token(vault=vault)
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
