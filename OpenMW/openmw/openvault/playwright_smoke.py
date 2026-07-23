"""Playwright smoke gate — real runner with artifact + dry-run fallback for CI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import structlog

from openmw.openvault.paths import ensure_home

log = structlog.get_logger()

SmokeStatus = Literal["pass", "fail", "pending", "skipped"]


@dataclass(frozen=True)
class SmokeResult:
    smoke_id: str
    url: str
    status: SmokeStatus
    mode: str
    detail: str
    artifact_path: str | None
    checks: list[dict[str, Any]]
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _smoke_dir() -> Path:
    path = ensure_home() / "playwright-smoke"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_artifact(smoke_id: str, payload: dict[str, Any]) -> Path:
    path = _smoke_dir() / f"{smoke_id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # Also write a human-readable fail/debug companion
    md = _smoke_dir() / f"{smoke_id}.md"
    lines = [
        f"# Playwright smoke `{smoke_id}`",
        "",
        f"- url: `{payload.get('url')}`",
        f"- status: **{payload.get('status')}**",
        f"- mode: `{payload.get('mode')}`",
        f"- detail: {payload.get('detail')}",
        "",
        "## Checks",
    ]
    for check in payload.get("checks", []):
        lines.append(f"- `{check.get('name')}` → {check.get('status')}: {check.get('detail')}")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _httpx_probe(url: str) -> dict[str, Any]:
    try:
        import httpx
    except ImportError:
        return {"name": "http_get", "status": "fail", "detail": "httpx not installed"}
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(url)
        ok = 200 <= resp.status_code < 500
        return {
            "name": "http_get",
            "status": "pass" if ok else "fail",
            "detail": f"HTTP {resp.status_code} in {resp.elapsed.total_seconds() * 1000:.0f}ms",
        }
    except Exception as exc:
        return {"name": "http_get", "status": "fail", "detail": str(exc)}


def _playwright_python(url: str) -> dict[str, Any]:
    """Run a minimal headed-off page.goto smoke when playwright is installed."""
    try:
        import importlib

        sync_api = importlib.import_module("playwright.sync_api")
        sync_playwright = sync_api.sync_playwright
    except ImportError:
        return {
            "name": "playwright_python",
            "status": "skipped",
            "detail": "playwright package not installed",
        }
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = page.title()
            status_code = resp.status if resp is not None else 0
            browser.close()
        ok = status_code > 0 and status_code < 500
        return {
            "name": "playwright_python",
            "status": "pass" if ok else "fail",
            "detail": f"status={status_code} title={title!r}",
        }
    except Exception as exc:
        return {"name": "playwright_python", "status": "fail", "detail": str(exc)}


def _playwright_cli(url: str, workdir: Path) -> dict[str, Any]:
    cli = shutil.which("playwright")
    npx = shutil.which("npx")
    script = workdir / "smoke.spec.js"
    script.write_text(
        f"""
const {{ test, expect }} = require('@playwright/test');
test('openvault smoke', async ({{ page }}) => {{
  const resp = await page.goto({url!r}, {{ waitUntil: 'domcontentloaded' }});
  expect(resp && resp.ok()).toBeTruthy();
}});
""",
        encoding="utf-8",
    )
    if cli:
        cmd = [cli, "test", str(script)]
    elif npx:
        cmd = ["npx", "--yes", "playwright", "test", str(script)]
    else:
        return {
            "name": "playwright_cli",
            "status": "skipped",
            "detail": "playwright CLI / npx not found",
        }
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=120.0,
            cwd=str(workdir),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": "playwright_cli", "status": "fail", "detail": str(exc)}
    detail = ((proc.stdout or "") + (proc.stderr or "")).strip()[:1500]
    return {
        "name": "playwright_cli",
        "status": "pass" if proc.returncode == 0 else "fail",
        "detail": detail or f"exit={proc.returncode}",
    }


def run_playwright_smoke(
    url: str,
    *,
    mode: str | None = None,
) -> SmokeResult:
    """Execute smoke gate.

    Modes:
      - ``auto`` (default): playwright python → CLI → httpx dry probe
      - ``playwright``: require real browser runner
      - ``dry``: httpx only (CI / no browser)
    """
    smoke_id = uuid.uuid4().hex[:12]
    resolved_mode = (mode or os.environ.get("OPENVAULT_PLAYWRIGHT_MODE", "auto")).lower()
    checks: list[dict[str, Any]] = []

    if not _valid_url(url):
        result = SmokeResult(
            smoke_id=smoke_id,
            url=url,
            status="fail",
            mode=resolved_mode,
            detail="invalid url — need http(s)://host",
            artifact_path=None,
            checks=[{"name": "url", "status": "fail", "detail": "invalid"}],
            created_at=time.time(),
        )
        path = _write_artifact(smoke_id, result.to_dict())
        return SmokeResult(**{**result.to_dict(), "artifact_path": str(path)})

    if resolved_mode in ("auto", "playwright"):
        py = _playwright_python(url)
        checks.append(py)
        if py["status"] == "pass":
            status: SmokeStatus = "pass"
            detail = py["detail"]
            used = "playwright_python"
        elif py["status"] == "fail" and resolved_mode == "playwright":
            status = "fail"
            detail = py["detail"]
            used = "playwright_python"
        else:
            work = _smoke_dir() / smoke_id
            work.mkdir(parents=True, exist_ok=True)
            cli = _playwright_cli(url, work)
            checks.append(cli)
            if cli["status"] == "pass":
                status = "pass"
                detail = cli["detail"]
                used = "playwright_cli"
            elif resolved_mode == "playwright":
                status = "fail"
                detail = cli["detail"] if cli["status"] != "skipped" else py["detail"]
                used = "playwright"
            else:
                probe = _httpx_probe(url)
                checks.append(probe)
                status = "pass" if probe["status"] == "pass" else "fail"
                detail = f"dry fallback: {probe['detail']}"
                used = "dry"
    else:
        probe = _httpx_probe(url)
        checks.append(probe)
        status = "pass" if probe["status"] == "pass" else "fail"
        detail = probe["detail"]
        used = "dry"

    payload = {
        "smoke_id": smoke_id,
        "url": url,
        "status": status,
        "mode": used,
        "detail": detail,
        "checks": checks,
        "created_at": time.time(),
    }
    artifact = _write_artifact(smoke_id, payload)
    log.info(
        "playwright_smoke",
        smoke_id=smoke_id,
        status=status,
        mode=used,
        artifact=str(artifact),
    )
    return SmokeResult(
        smoke_id=smoke_id,
        url=url,
        status=status,
        mode=used,
        detail=detail,
        artifact_path=str(artifact),
        checks=checks,
        created_at=float(payload["created_at"]),
    )


def load_smoke(smoke_id: str) -> dict[str, Any] | None:
    path = _smoke_dir() / f"{smoke_id}.json"
    if not path.is_file():
        return None
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None
    return {str(k): v for k, v in raw.items()}
