"""Coolify — second real host adapter (founder shortlist after Cloudflare Pages).

Coolify is self-hosted PaaS: the user runs the instance, vaults a Bearer token,
and points us at an application UUID. We trigger a redeploy via Coolify's API
and return only a URL Coolify itself reports (deployment_url or application
fqdn). We never invent ``https://{something}``.

Unlike Cloudflare Pages Direct Upload, Coolify builds from the application's
configured source on the Coolify server — ``artifact_dir`` is not uploaded.
That is intentional honesty, not a shortcut.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from openmw.openvault.ship.hosts.base import DeployResult, DomainResult, Preflight

log = structlog.get_logger()

_API_TIMEOUT_S = 30.0
_DEPLOY_POLL_S = 2.0
_DEPLOY_TIMEOUT_S = 600.0
_SAFE_NAME = re.compile(r"[^a-z0-9-]+")

#: Terminal statuses Coolify has used across recent versions.
_DONE_OK = frozenset({"finished", "success", "succeeded", "done"})
_DONE_FAIL = frozenset(
    {"failed", "error", "cancelled", "canceled", "cancelled-by-user", "canceled-by-user"}
)


def normalize_base_url(raw: str) -> str:
    """Strip trailing slash; require http(s). Empty in → empty out."""
    url = (raw or "").strip().rstrip("/")
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return url


def normalize_project_name(raw: str) -> str:
    name = _SAFE_NAME.sub("-", raw.strip().lower()).strip("-")
    name = re.sub(r"-{2,}", "-", name)
    return name or "openvault-app"


def observed_url_from_fqdn(fqdn: str) -> str:
    """Turn Coolify's fqdn field into a single openable URL, or empty.

    Coolify may return ``https://a.com,https://b.com`` or a bare hostname.
    We take the first non-empty segment; we never invent a host.
    """
    raw = (fqdn or "").strip()
    if not raw:
        return ""
    first = raw.split(",")[0].strip()
    if not first:
        return ""
    if first.startswith("http://") or first.startswith("https://"):
        return first.rstrip("/")
    # Bare hostname Coolify already assigned — scheme only, not a guessed domain.
    return f"https://{first.rstrip('/')}"


class CoolifyAdapter:
    """Trigger a deploy on the user's Coolify instance; report only observed URLs."""

    id = "coolify"
    name = "Coolify"
    credential_provider = "coolify"
    # Coolify builds from the application's own configured source.
    needs_local_build = False

    def __init__(
        self,
        *,
        base_url: str | None,
        api_token: str | None,
        app_uuid: str | None = None,
    ) -> None:
        self._base = normalize_base_url(base_url or "")
        self._token = (api_token or "").strip()
        self._app_uuid = (app_uuid or "").strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _api(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, Any, str]:
        """Call Coolify. Returns (ok, json_body_or_dict, human error)."""
        if not self._base:
            return False, {}, "Coolify base URL is not set"
        url = f"{self._base}/api/v1{path}"
        try:
            resp = httpx.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=payload,
                timeout=_API_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            return False, {}, f"could not reach Coolify: {exc}"

        if resp.status_code in (401, 403):
            return False, {}, f"Coolify rejected the token (HTTP {resp.status_code})"
        if resp.status_code == 404:
            return False, {}, f"Coolify resource not found (HTTP 404) at {path}"

        try:
            body: Any = resp.json() if resp.content else {}
        except ValueError:
            return False, {}, f"Coolify returned non-JSON (HTTP {resp.status_code})"

        if resp.status_code >= 400:
            if isinstance(body, dict):
                msg = body.get("message") or body.get("error") or f"HTTP {resp.status_code}"
            else:
                msg = f"HTTP {resp.status_code}"
            return False, body, str(msg)
        return True, body, ""

    def preflight(self) -> Preflight:
        if not self._base:
            return Preflight(
                ready=False,
                blocker=(
                    "Set your Coolify instance URL (e.g. https://coolify.example.com) "
                    "as COOLIFY_URL — OpenVault talks to your Coolify, not ours."
                ),
            )
        if not self._token:
            return Preflight(
                ready=False,
                blocker=(
                    "Add a Coolify API token to the vault (provider id `coolify`) or "
                    "set COOLIFY_TOKEN. Create one in Coolify → Keys & Tokens → API tokens "
                    "with deploy permission."
                ),
            )
        if not self._app_uuid:
            return Preflight(
                ready=False,
                blocker=(
                    "Set COOLIFY_APP_UUID to the application UUID from Coolify "
                    "(Application → Configuration → UUID). Coolify deploys an existing "
                    "app by UUID — we do not invent one."
                ),
            )

        # Reachability + auth. /teams is stable across recent Coolify versions.
        ok, body, err = self._api("GET", "/teams")
        if not ok:
            return Preflight(
                ready=False,
                blocker=f"Coolify instance not usable: {err}",
                facts={"base_url": self._base},
            )

        facts: dict[str, str] = {"base_url": self._base}
        if isinstance(body, list) and body:
            first = body[0] if isinstance(body[0], dict) else {}
            if first.get("name"):
                facts["team"] = str(first["name"])

        ok, app, err = self._api("GET", f"/applications/{self._app_uuid}")
        if not ok:
            return Preflight(
                ready=False,
                blocker=f"Coolify application {self._app_uuid} not reachable: {err}",
                facts=facts,
            )
        if isinstance(app, dict):
            if app.get("name"):
                facts["app_name"] = str(app["name"])
            if app.get("uuid"):
                facts["app_uuid"] = str(app["uuid"])
            fqdn = observed_url_from_fqdn(str(app.get("fqdn") or ""))
            if fqdn:
                facts["current_fqdn"] = fqdn

        return Preflight(ready=True, facts=facts)

    def _resolve_app_uuid(self, project: str) -> tuple[str, str]:
        """Return (uuid, error). Prefer configured UUID; else match by name."""
        if self._app_uuid:
            return self._app_uuid, ""
        name = normalize_project_name(project)
        ok, body, err = self._api("GET", "/applications")
        if not ok:
            return "", f"could not list Coolify applications: {err}"
        apps = body if isinstance(body, list) else []
        for app in apps:
            if not isinstance(app, dict):
                continue
            app_name = normalize_project_name(str(app.get("name") or ""))
            if app_name == name and app.get("uuid"):
                return str(app["uuid"]), ""
        return "", (
            f"no Coolify application named '{name}' — set COOLIFY_APP_UUID or create "
            "the app in Coolify first"
        )

    def _poll_deployment(self, deployment_uuid: str) -> tuple[bool, dict[str, Any], str]:
        """Wait until Coolify reports a terminal status. Returns (ok, body, detail)."""
        deadline = time.monotonic() + _DEPLOY_TIMEOUT_S
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            ok, body, err = self._api("GET", f"/deployments/{deployment_uuid}")
            if not ok:
                return False, {}, f"lost deployment status: {err}"
            last = body if isinstance(body, dict) else {}
            status = str(last.get("status") or "").strip().lower()
            if status in _DONE_OK:
                return True, last, status
            if status in _DONE_FAIL:
                return False, last, f"Coolify deployment {status}"
            time.sleep(_DEPLOY_POLL_S)
        return False, last, f"deployment timed out after {int(_DEPLOY_TIMEOUT_S / 60)} minutes"

    def deploy(self, artifact_dir: Path, *, project: str) -> DeployResult:
        # artifact_dir is part of HostAdapter; Coolify does not Direct-Upload it.
        # We still refuse nonsense paths only when the caller passed one that
        # claims to be a directory but isn't — empty/missing is fine here.
        if artifact_dir and Path(artifact_dir).exists() and not Path(artifact_dir).is_dir():
            return DeployResult(
                ok=False,
                detail=f"artifact path exists but is not a directory: {artifact_dir}",
            )

        pre = self.preflight()
        if not pre.ready:
            return DeployResult(ok=False, detail=pre.blocker)

        uuid, err = self._resolve_app_uuid(project)
        if not uuid:
            return DeployResult(ok=False, detail=err)

        ok, body, err = self._api("POST", "/deploy", params={"uuid": uuid})
        if not ok:
            return DeployResult(ok=False, detail=f"Coolify deploy refused: {err}")

        deployments = []
        if isinstance(body, dict):
            deployments = body.get("deployments") or []
        if not deployments:
            return DeployResult(
                ok=False,
                detail=(
                    "Coolify accepted the request but returned no deployment UUID — "
                    "refusing to invent a live URL"
                ),
                log=str(body),
            )
        first = deployments[0] if isinstance(deployments[0], dict) else {}
        dep_uuid = str(first.get("deployment_uuid") or "").strip()
        if not dep_uuid:
            return DeployResult(
                ok=False,
                detail="Coolify returned a deployment entry without deployment_uuid",
                log=str(body),
            )

        done, dep_body, dep_detail = self._poll_deployment(dep_uuid)
        logs = str(dep_body.get("logs") or "")
        if not done:
            return DeployResult(ok=False, detail=dep_detail, deployment_ref=dep_uuid, log=logs)

        # Prefer URL Coolify stamped on the deployment; fall back to app fqdn.
        url = observed_url_from_fqdn(str(dep_body.get("deployment_url") or ""))
        if not url:
            ok, app, err = self._api("GET", f"/applications/{uuid}")
            if ok and isinstance(app, dict):
                url = observed_url_from_fqdn(str(app.get("fqdn") or ""))

        if not url:
            return DeployResult(
                ok=False,
                deployment_ref=dep_uuid,
                detail=(
                    "Coolify reported a finished deploy but no deployment_url or "
                    "application fqdn — treating that as failure rather than guessing"
                ),
                log=logs,
            )

        log.info("coolify_deployed", app_uuid=uuid, url=url, deployment=dep_uuid)
        return DeployResult(
            ok=True,
            url=url,
            deployment_ref=dep_uuid,
            detail=(
                f"Live at {url} (Coolify rebuilt application {uuid} from its "
                "configured source — local artifact was not uploaded)"
            ),
            log=logs,
        )

    def attach_domain(self, *, project: str, hostname: str) -> DomainResult:
        """Coolify owns FQDN on the application — we do not invent DNS for it.

        If the app already shows this hostname, success. Otherwise tell the user
        to set the domain in Coolify (API shapes vary across self-hosted versions).
        """
        host = hostname.strip().lower().rstrip(".")
        if not host:
            return DomainResult(ok=False, detail="no hostname given")

        uuid, err = self._resolve_app_uuid(project)
        if not uuid:
            return DomainResult(ok=False, hostname=host, detail=err)

        ok, app, err = self._api("GET", f"/applications/{uuid}")
        if not ok or not isinstance(app, dict):
            return DomainResult(
                ok=False,
                hostname=host,
                detail=f"could not read Coolify application: {err}",
            )

        fqdn_field = str(app.get("fqdn") or "")
        if host in fqdn_field.lower() or observed_url_from_fqdn(fqdn_field).endswith(host):
            return DomainResult(
                ok=True,
                hostname=host,
                detail=f"{host} is already on this Coolify application",
            )

        return DomainResult(
            ok=False,
            hostname=host,
            detail=(
                f"Set {host} as the FQDN on this Coolify application in the Coolify "
                "UI (Domains), then retry. OpenVault does not invent Coolify DNS."
            ),
            required_records=[
                {
                    "type": "CNAME_or_A",
                    "name": host,
                    "value": "(Coolify server IP or shown CNAME)",
                    "note": "Configure the domain on the Coolify application first",
                }
            ],
        )


def from_vault(
    get_secret_for_provider,
    *,
    base_url: str | None = None,
    app_uuid: str | None = None,
) -> CoolifyAdapter:
    """Build from vault + env. Callable keeps this module free of vault imports."""
    token = get_secret_for_provider("coolify")
    return CoolifyAdapter(
        base_url=base_url or os.environ.get("COOLIFY_URL"),
        api_token=token or os.environ.get("COOLIFY_TOKEN"),
        app_uuid=app_uuid or os.environ.get("COOLIFY_APP_UUID"),
    )


__all__ = [
    "CoolifyAdapter",
    "from_vault",
    "normalize_base_url",
    "normalize_project_name",
    "observed_url_from_fqdn",
]
