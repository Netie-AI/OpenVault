"""Netlify — third real host adapter (founder shortlist after Coolify).

Static/host pattern adjacent to Cloudflare Pages: the user's machine builds,
we zip the artifact, and Netlify's REST API accepts a Direct Upload deploy.
We return only a URL Netlify itself reports (``ssl_url`` / ``deploy_ssl_url`` /
``url``). We never invent ``https://{name}.netlify.app``.

Auth is a personal access token (vault provider ``netlify`` or
``NETLIFY_AUTH_TOKEN``). Site id is optional — when set via ``NETLIFY_SITE_ID``
we deploy that site; otherwise we look up or create by project name.
"""

from __future__ import annotations

import io
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import structlog

from openmw.openvault.ship.hosts.base import DeployResult, DomainResult, Preflight

log = structlog.get_logger()

API_ROOT = "https://api.netlify.com/api/v1"

_API_TIMEOUT_S = 30.0
_UPLOAD_TIMEOUT_S = 300.0
_DEPLOY_POLL_S = 2.0
_DEPLOY_TIMEOUT_S = 600.0
_SAFE_NAME = re.compile(r"[^a-z0-9-]+")

_DONE_OK = frozenset({"ready"})
_DONE_FAIL = frozenset({"error", "failed"})


def normalize_project_name(raw: str) -> str:
    """Netlify site names: lowercase alphanumerics and dashes."""
    name = _SAFE_NAME.sub("-", raw.strip().lower()).strip("-")
    name = re.sub(r"-{2,}", "-", name)
    return name or "openvault-app"


def observed_url_from_deploy(body: dict[str, Any]) -> str:
    """Pick the first non-empty URL Netlify stamped on the deploy/site object.

    Order prefers TLS URLs Netlify already assigned. Empty when none present —
    callers must treat that as failure, not invent a ``*.netlify.app`` host.
    """
    for key in ("ssl_url", "deploy_ssl_url", "url", "deploy_url"):
        raw = str(body.get(key) or "").strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw.rstrip("/")
    return ""


def _zip_directory(artifact_dir: Path) -> bytes:
    """Zip artifact contents with paths relative to the directory root."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(artifact_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(artifact_dir).as_posix())
    return buf.getvalue()


class NetlifyAdapter:
    """Publish a built directory to the user's own Netlify account."""

    id = "netlify"
    name = "Netlify"
    credential_provider = "netlify"
    # Zip Direct Upload sends a built directory from this machine.
    needs_local_build = True

    def __init__(
        self,
        *,
        api_token: str | None,
        site_id: str | None = None,
    ) -> None:
        self._token = (api_token or "").strip()
        self._site_id = (site_id or "").strip()

    def _headers(self, *, content_type: str | None = "application/json") -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _api(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        content: bytes | None = None,
        content_type: str | None = "application/json",
        timeout: float = _API_TIMEOUT_S,
    ) -> tuple[bool, Any, str]:
        """Call Netlify. Returns (ok, json_body_or_dict, human error)."""
        url = f"{API_ROOT}{path}"
        try:
            kwargs: dict[str, Any] = {
                "headers": self._headers(content_type=content_type),
                "params": params,
                "timeout": timeout,
            }
            if content is not None:
                kwargs["content"] = content
            elif payload is not None:
                kwargs["json"] = payload
            resp = httpx.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            return False, {}, f"could not reach Netlify: {exc}"

        if resp.status_code in (401, 403):
            return False, {}, f"Netlify rejected the token (HTTP {resp.status_code})"
        if resp.status_code == 404:
            return False, {}, f"Netlify resource not found (HTTP 404) at {path}"

        try:
            body: Any = resp.json() if resp.content else {}
        except ValueError:
            return False, {}, f"Netlify returned non-JSON (HTTP {resp.status_code})"

        if resp.status_code >= 400:
            if isinstance(body, dict):
                msg = body.get("message") or body.get("error") or f"HTTP {resp.status_code}"
            else:
                msg = f"HTTP {resp.status_code}"
            return False, body, str(msg)
        return True, body, ""

    def preflight(self) -> Preflight:
        if not self._token:
            return Preflight(
                ready=False,
                blocker=(
                    "Add a Netlify personal access token to the vault (provider id "
                    "`netlify`) or set NETLIFY_AUTH_TOKEN. Create one at "
                    "app.netlify.com/user/applications#personal-access-tokens."
                ),
            )

        ok, body, err = self._api("GET", "/user")
        if not ok:
            return Preflight(ready=False, blocker=f"Netlify token not usable: {err}")

        facts: dict[str, str] = {}
        if isinstance(body, dict):
            if body.get("email"):
                facts["email"] = str(body["email"])
            if body.get("full_name"):
                facts["name"] = str(body["full_name"])
            elif body.get("slug"):
                facts["slug"] = str(body["slug"])

        if self._site_id:
            ok, site, err = self._api("GET", f"/sites/{self._site_id}")
            if not ok:
                return Preflight(
                    ready=False,
                    blocker=f"Netlify site {self._site_id} not reachable: {err}",
                    facts=facts,
                )
            if isinstance(site, dict):
                if site.get("name"):
                    facts["site_name"] = str(site["name"])
                if site.get("id"):
                    facts["site_id"] = str(site["id"])
                url = observed_url_from_deploy(site)
                if url:
                    facts["current_url"] = url

        return Preflight(ready=True, facts=facts)

    def _resolve_site(self, project: str) -> tuple[str, str]:
        """Return (site_id, error). Prefer configured id; else match/create by name."""
        if self._site_id:
            return self._site_id, ""

        name = normalize_project_name(project)
        ok, body, err = self._api("GET", "/sites", params={"name": name})
        if not ok:
            return "", f"could not list Netlify sites: {err}"

        sites = body if isinstance(body, list) else []
        for site in sites:
            if not isinstance(site, dict):
                continue
            site_name = normalize_project_name(str(site.get("name") or ""))
            if site_name == name and site.get("id"):
                return str(site["id"]), ""

        ok, created, err = self._api("POST", "/sites", payload={"name": name})
        if not ok or not isinstance(created, dict) or not created.get("id"):
            return "", (
                f"could not create Netlify site '{name}': {err or 'no id in response'} "
                "— set NETLIFY_SITE_ID to an existing site"
            )
        return str(created["id"]), ""

    def _poll_deploy(self, deploy_id: str) -> tuple[bool, dict[str, Any], str]:
        """Wait until Netlify reports ready/error. Returns (ok, body, detail)."""
        deadline = time.monotonic() + _DEPLOY_TIMEOUT_S
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            ok, body, err = self._api("GET", f"/deploys/{deploy_id}")
            if not ok:
                return False, {}, f"lost deployment status: {err}"
            last = body if isinstance(body, dict) else {}
            state = str(last.get("state") or "").strip().lower()
            if state in _DONE_OK:
                return True, last, state
            if state in _DONE_FAIL:
                msg = str(last.get("error_message") or last.get("error") or state)
                return False, last, f"Netlify deployment {msg}"
            time.sleep(_DEPLOY_POLL_S)
        return False, last, f"deployment timed out after {int(_DEPLOY_TIMEOUT_S / 60)} minutes"

    def deploy(self, artifact_dir: Path, *, project: str) -> DeployResult:
        artifact = Path(artifact_dir)
        if not artifact.is_dir():
            return DeployResult(
                ok=False,
                detail=f"nothing to upload — {artifact} is not a directory",
            )
        if not any(artifact.iterdir()):
            return DeployResult(
                ok=False,
                detail=(
                    f"nothing to upload — {artifact} is empty. The build step "
                    "probably produced its output somewhere else; check the "
                    "detected output directory."
                ),
            )

        pre = self.preflight()
        if not pre.ready:
            return DeployResult(ok=False, detail=pre.blocker)

        site_id, err = self._resolve_site(project)
        if not site_id:
            return DeployResult(ok=False, detail=err)

        try:
            zip_bytes = _zip_directory(artifact)
        except OSError as exc:
            return DeployResult(ok=False, detail=f"could not zip artifact: {exc}")
        if not zip_bytes:
            return DeployResult(ok=False, detail="zipped artifact was empty")

        ok, body, err = self._api(
            "POST",
            f"/sites/{site_id}/deploys",
            content=zip_bytes,
            content_type="application/zip",
            timeout=_UPLOAD_TIMEOUT_S,
        )
        if not ok:
            return DeployResult(ok=False, detail=f"Netlify deploy refused: {err}")

        deploy = body if isinstance(body, dict) else {}
        deploy_id = str(deploy.get("id") or "").strip()
        if not deploy_id:
            return DeployResult(
                ok=False,
                detail=(
                    "Netlify accepted the upload but returned no deploy id — "
                    "refusing to invent a live URL"
                ),
                log=str(body),
            )

        state = str(deploy.get("state") or "").strip().lower()
        if state not in _DONE_OK and state not in _DONE_FAIL:
            done, deploy, dep_detail = self._poll_deploy(deploy_id)
            if not done:
                return DeployResult(
                    ok=False,
                    detail=dep_detail,
                    deployment_ref=deploy_id,
                    log=str(deploy.get("error_message") or ""),
                )
        elif state in _DONE_FAIL:
            msg = str(deploy.get("error_message") or deploy.get("error") or state)
            return DeployResult(
                ok=False,
                detail=f"Netlify deployment {msg}",
                deployment_ref=deploy_id,
            )

        url = observed_url_from_deploy(deploy)
        if not url:
            ok, site, _ = self._api("GET", f"/sites/{site_id}")
            if ok and isinstance(site, dict):
                url = observed_url_from_deploy(site)

        if not url:
            return DeployResult(
                ok=False,
                deployment_ref=deploy_id,
                detail=(
                    "Netlify reported a ready deploy but no ssl_url / deploy_ssl_url "
                    "/ url — treating that as failure rather than guessing"
                ),
                log=str(deploy),
            )

        log.info("netlify_deployed", site_id=site_id, url=url, deployment=deploy_id)
        return DeployResult(
            ok=True,
            url=url,
            deployment_ref=deploy_id,
            detail=f"Live at {url}",
            log=str(deploy.get("state") or "ready"),
        )

    def attach_domain(self, *, project: str, hostname: str) -> DomainResult:
        """Register a custom domain on the Netlify site when possible.

        Netlify creates DNS for zones it hosts; otherwise we return the CNAME
        to paste. We never invent a live URL here.
        """
        host = hostname.strip().lower().rstrip(".")
        if not host:
            return DomainResult(ok=False, detail="no hostname given")

        site_id, err = self._resolve_site(project)
        if not site_id:
            return DomainResult(ok=False, hostname=host, detail=err)

        ok, site, err = self._api("GET", f"/sites/{site_id}")
        if not ok or not isinstance(site, dict):
            return DomainResult(
                ok=False,
                hostname=host,
                detail=f"could not read Netlify site: {err}",
            )

        existing = str(site.get("custom_domain") or "").strip().lower()
        aliases = site.get("domain_aliases") or []
        alias_list = [str(a).lower() for a in aliases] if isinstance(aliases, list) else []
        if host == existing or host in alias_list:
            return DomainResult(
                ok=True,
                hostname=host,
                detail=f"{host} is already on this Netlify site",
            )

        # Prefer alias when a primary custom_domain already exists.
        if existing:
            ok, _, err = self._api(
                "POST",
                f"/sites/{site_id}/domains/{host}",
            )
        else:
            ok, _, err = self._api(
                "PATCH",
                f"/sites/{site_id}",
                payload={"custom_domain": host},
            )

        default_domain = str(site.get("default_domain") or "").strip()
        # CNAME target must be observed from Netlify — never invent *.netlify.app.
        records: list[dict[str, str]] = []
        if default_domain:
            records = [
                {
                    "type": "CNAME",
                    "name": host,
                    "value": default_domain,
                    "note": "Only if the domain is registered outside Netlify",
                }
            ]

        if ok or "already" in err.lower():
            return DomainResult(
                ok=True,
                hostname=host,
                detail=(
                    f"{host} attached on Netlify. If DNS is not on Netlify, add the "
                    "CNAME Netlify shows for this site at your registrar."
                ),
                required_records=records,
            )

        return DomainResult(
            ok=False,
            hostname=host,
            detail=(
                f"Netlify would not attach {host} automatically ({err}). "
                "Configure Domain management in the Netlify UI, then retry."
            ),
            required_records=records
            or [
                {
                    "type": "CNAME",
                    "name": host,
                    "value": "(Netlify default domain shown in Site settings)",
                    "note": "OpenVault does not invent *.netlify.app hosts",
                }
            ],
        )


def from_vault(
    get_secret_for_provider,
    *,
    site_id: str | None = None,
) -> NetlifyAdapter:
    """Build from vault + env. Callable keeps this module free of vault imports."""
    token = get_secret_for_provider("netlify")
    return NetlifyAdapter(
        api_token=token or os.environ.get("NETLIFY_AUTH_TOKEN"),
        site_id=site_id or os.environ.get("NETLIFY_SITE_ID"),
    )


__all__ = [
    "NetlifyAdapter",
    "from_vault",
    "normalize_project_name",
    "observed_url_from_deploy",
]
