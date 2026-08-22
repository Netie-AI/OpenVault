"""Local folder / GitHub URL inspect for the ship library picker."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from openmw.openvault.paths import ensure_home
from openmw.openvault.ship.detect import DetectionInputError, detect_project


@dataclass
class UploadSession:
    session_id: str
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def library_home() -> dict[str, Any]:
    return {
        "home": str(ensure_home()),
        "hint": "POST /api/ship/library/inspect with an absolute path or github_url",
    }


def inspect_folder(path: str) -> dict[str, Any]:
    try:
        stack = detect_project(path)
    except DetectionInputError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "path": path, "stack": stack.to_dict()}


def inspect_github_url(github_url: str) -> dict[str, Any]:
    return {
        "ok": True,
        "github_url": github_url,
        "detail": (
            "Clone locally, then inspect the absolute path. Origin can also host the git copy."
        ),
    }


def create_upload_session() -> UploadSession:
    return UploadSession(session_id=uuid.uuid4().hex[:12], created_at=time.time())


def scan_upload_session(session_id: str) -> dict[str, Any]:
    return {"session_id": session_id, "files": [], "detail": "browser uploads are not a ship path"}
