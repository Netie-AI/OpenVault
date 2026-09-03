"""Library picker — stolen from FreeBuild /library (folder · GitHub · URL)."""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from openmw.openvault.paths import ensure_home
from openmw.openvault.ship.cicd import detect_cicd
from openmw.openvault.ship.detect import detect_project
from openmw.openvault.ship.github_auth import list_repos, parse_github_url

SourceKind = Literal["folder", "github_url", "github_repo", "upload_session"]


@dataclass
class LibraryItem:
    id: str
    kind: SourceKind
    title: str
    path_or_url: str
    stack_primary: str = "unknown"
    stack_confidence: float = 0.0
    cicd_status: str = "missing"
    default_branch: str = "main"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UploadSession:
    session_id: str
    created_at: float = field(default_factory=time.time)
    staging_dir: str = ""
    scanned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _uploads_dir() -> Path:
    path = ensure_home() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_upload_session() -> UploadSession:
    """FreeBuild-style folder session — operator copies files into staging."""
    sid = uuid.uuid4().hex[:12]
    staging = _uploads_dir() / sid
    staging.mkdir(parents=True, exist_ok=True)
    session = UploadSession(session_id=sid, staging_dir=str(staging))
    meta = staging / ".session.json"
    meta.write_text(
        __import__("json").dumps(session.to_dict(), indent=2),
        encoding="utf-8",
    )
    return session


def scan_upload_session(session_id: str) -> dict[str, Any]:
    staging = _uploads_dir() / session_id
    if not staging.is_dir():
        return {"ok": False, "error": "session not found"}
    stack = detect_project(staging)
    cicd = detect_cicd(staging)
    file_count = sum(1 for p in staging.rglob("*") if p.is_file() and p.name != ".session.json")
    return {
        "ok": True,
        "session_id": session_id,
        "staging_dir": str(staging),
        "stack": stack.to_dict(),
        "cicd": cicd.to_dict(),
        "file_count": file_count,
        "hint": "Drop a zip or folder via the Ship UI, or copy into staging_dir.",
    }


def receive_upload_bytes(
    session_id: str,
    *,
    filename: str,
    data: bytes,
    relative_path: str = "",
) -> dict[str, Any]:
    """Write one uploaded blob into the staging dir (zip expands; else single file)."""
    import io
    import zipfile

    staging = _uploads_dir() / session_id
    if not staging.is_dir():
        return {"ok": False, "error": "session not found"}

    name = (relative_path or filename or "upload.bin").replace("\\", "/").lstrip("/")
    if ".." in name.split("/"):
        return {"ok": False, "error": "path traversal rejected"}

    wrote = 0
    if name.lower().endswith(".zip") or filename.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    member = info.filename.replace("\\", "/")
                    if member.startswith("/") or ".." in member.split("/"):
                        continue
                    dest = staging / member
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(info))
                    wrote += 1
        except zipfile.BadZipFile:
            return {"ok": False, "error": "invalid zip"}
    else:
        dest = staging / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        wrote = 1

    return {
        "ok": True,
        "session_id": session_id,
        "staging_dir": str(staging),
        "files_written": wrote,
    }


def inspect_folder(path: str) -> dict[str, Any]:
    stack = detect_project(path)
    cicd = detect_cicd(path)
    item = LibraryItem(
        id=uuid.uuid4().hex[:10],
        kind="folder",
        title=Path(stack.project_path).name,
        path_or_url=stack.project_path,
        stack_primary=stack.primary,
        stack_confidence=stack.confidence,
        cicd_status=cicd.status,
        detail=" → ".join(stack.suggested_build) if stack.suggested_build else "",
    )
    return {
        "ok": stack.primary != "unknown",
        "item": item.to_dict(),
        "stack": stack.to_dict(),
        "cicd": cicd.to_dict(),
    }


def inspect_github_url(url: str) -> dict[str, Any]:
    parsed = parse_github_url(url)
    if not parsed:
        return {"ok": False, "error": "Not a github.com/owner/repo URL"}
    owner, repo = parsed
    html = f"https://github.com/{owner}/{repo}"
    item = LibraryItem(
        id=f"{owner}-{repo}"[:24],
        kind="github_url",
        title=f"{owner}/{repo}",
        path_or_url=html,
        detail="Will clone via gh/git when ship engine runs",
    )
    return {
        "ok": True,
        "item": item.to_dict(),
        "owner": owner,
        "repo": repo,
        "html_url": html,
        "clone_url": f"https://github.com/{owner}/{repo}.git",
    }


def library_home(*, repo_limit: int = 30) -> dict[str, Any]:
    """FreeBuild library home: connection + repos + how to add folder/URL."""
    from openmw.openvault.ship.github_auth import connection_status

    conn = connection_status()
    repos_payload = (
        list_repos(limit=repo_limit)
        if conn.connected
        else {
            "ok": False,
            "repos": [],
            "connection": conn.to_dict(),
        }
    )
    return {
        "connection": conn.to_dict(),
        "repos": repos_payload.get("repos", []),
        "tabs": ["folder", "github", "url", "upload"],
        "notes": [
            "Folder: paste absolute path or Choose folder (then paste full path).",
            "GitHub: connect via gh CLI (repo+workflow+read:org) then pick a repo.",
            "URL: paste https://github.com/org/repo — public works without auth.",
            "Upload: create session → copy tree into staging → scan → Deploy.",
        ],
        "stolen_from": "vendor/openship apps/dashboard library + github local-auth",
    }


def ensure_local_clone(github_url: str, *, branch: str = "main") -> dict[str, Any]:
    """Clone or update repo under OPENVAULT_HOME/clones (uses gh/git)."""
    parsed = parse_github_url(github_url)
    if not parsed:
        return {"ok": False, "error": "invalid github url"}
    owner, repo = parsed
    dest = ensure_home() / "clones" / owner / repo
    dest.parent.mkdir(parents=True, exist_ok=True)
    gh = shutil.which("gh")
    git = shutil.which("git")
    if dest.is_dir() and (dest / ".git").is_dir():
        cmd = [git or "git", "-C", str(dest), "pull", "--ff-only"]
        try:
            proc = __import__("subprocess").run(
                cmd, check=False, capture_output=True, text=True, timeout=180.0
            )
            ok = proc.returncode == 0
            return {
                "ok": ok,
                "path": str(dest),
                "action": "pull",
                "detail": ((proc.stdout or "") + (proc.stderr or ""))[:1500],
            }
        except (OSError, __import__("subprocess").TimeoutExpired) as exc:
            return {"ok": False, "path": str(dest), "error": str(exc)}

    if gh:
        cmd = [gh, "repo", "clone", f"{owner}/{repo}", str(dest), "--", "-b", branch]
    elif git:
        cmd = [
            git,
            "clone",
            "--branch",
            branch,
            "--single-branch",
            f"https://github.com/{owner}/{repo}.git",
            str(dest),
        ]
    else:
        return {"ok": False, "error": "need gh or git on PATH"}
    try:
        proc = __import__("subprocess").run(
            cmd, check=False, capture_output=True, text=True, timeout=300.0
        )
    except (OSError, __import__("subprocess").TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    ok = proc.returncode == 0 and dest.is_dir()
    return {
        "ok": ok,
        "path": str(dest),
        "action": "clone",
        "detail": ((proc.stdout or "") + (proc.stderr or ""))[:1500],
    }
