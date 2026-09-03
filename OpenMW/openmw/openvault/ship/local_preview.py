"""Loopback preview of a built folder — not a live host URL.

Vercel mints ``*.vercel.app`` for every push. We refuse that. After a local
build we can still show the artifact on ``127.0.0.1`` so the operator can click
something. That URL is never ``public_url`` and never HT1.
"""

from __future__ import annotations

import threading
from contextlib import suppress
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import structlog

from openmw.openvault.ship.stacks import is_upload_ignored_path

log = structlog.get_logger()

_LOCK = threading.Lock()
_SERVERS: list[ThreadingHTTPServer] = []


class _PreviewHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        rel = unquote(urlparse(self.path).path).lstrip("/")
        name = Path(rel).name
        if rel and (is_upload_ignored_path(rel) or is_upload_ignored_path(name)):
            self.send_error(404, "not previewed")
            return
        super().do_GET()


def preview_root_for(work_path: Path, stack: object) -> Path | None:
    """Directory we may serve. Empty/missing output is not a fake site."""
    output_dir = str(getattr(stack, "output_directory", "") or "").strip()
    root = str(getattr(stack, "root_directory", "") or "").strip()
    base = work_path / root if root else work_path
    if output_dir:
        candidate = base / output_dir
        if _has_previewable_files(candidate):
            return candidate
    if (base / "index.html").is_file():
        return base
    if base != work_path and (work_path / "index.html").is_file():
        return work_path
    return None


def _has_previewable_files(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        if not is_upload_ignored_path(rel):
            return True
    return False


def preview_project(project_path: str) -> dict[str, Any]:
    """Preview a folder without deploying. Never a live host URL.

    Raises ``DetectionInputError`` for empty/relative paths (HTTP 400).
    """
    from openmw.openvault.ship.detect import detect_project

    stack = detect_project(project_path)
    root = preview_root_for(Path(stack.project_path), stack)
    empty: dict[str, Any] = {
        "ok": False,
        "local_preview_url": "",
        "public_url": None,
        "live": False,
        "live_public_url": None,
        "note": "Loopback only. Not live. Not HT1.",
    }
    if root is None:
        empty["blocker"] = (
            "No previewable artifact (need index.html or a built output_directory). "
            "Not a live URL."
        )
        return empty
    url = start_local_preview(root)
    if not url:
        empty["blocker"] = "Loopback preview bind failed. Not a live URL."
        return empty
    return {
        "ok": True,
        "local_preview_url": url,
        "public_url": None,
        "live": False,
        "live_public_url": None,
        "note": "Loopback only. Not live. Not HT1.",
    }


def start_local_preview(root: Path) -> str:
    """Bind ``127.0.0.1`` only. Returns ``http://127.0.0.1:<port>`` or empty."""
    resolved = root.resolve()
    if not _has_previewable_files(resolved):
        return ""
    handler = partial(_PreviewHandler, directory=str(resolved))
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    except OSError as exc:
        log.warning("local_preview_bind_failed", error=str(exc))
        return ""
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="ov-local-preview", daemon=True)
    thread.start()
    with _LOCK:
        _SERVERS.append(httpd)
    port = int(httpd.server_address[1])
    url = f"http://127.0.0.1:{port}"
    log.info("local_preview_started", port=port)
    return url


def stop_all_previews() -> None:
    with _LOCK:
        servers = list(_SERVERS)
        _SERVERS.clear()
    for httpd in servers:
        with suppress(Exception):
            httpd.shutdown()
        with suppress(Exception):
            httpd.server_close()


__all__ = [
    "preview_project",
    "preview_root_for",
    "start_local_preview",
    "stop_all_previews",
]
