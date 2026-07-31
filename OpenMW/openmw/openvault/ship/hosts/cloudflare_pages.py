"""Cloudflare Pages — the first real host adapter.

Chosen first on value-per-effort. It is the only mainstream target where a
user with no servers and no monthly budget can put a site on a domain they
already own: the free tier is genuinely free, Direct Upload means we never
need repo access or a git integration, and custom domains are a first-class
API object rather than a support ticket. Users buying domains at Cloudflare
get DNS wired automatically; users on Spaceship (or anywhere else) get the
exact records to paste, which is the honest outcome rather than a silent
failure.

Two execution strategies, tried in order:

1. ``wrangler pages deploy`` when the CLI is present. Direct Upload's asset
   protocol (hash negotiation, JWT-scoped upload, manifest commit) is
   involved and versioned; wrangler is the reference implementation and
   Cloudflare keeps it correct. Shelling out to it is not a shortcut, it is
   the supported path.
2. The REST API for everything wrangler is not needed for — verifying the
   token, creating the project, and attaching the domain.

If neither can run we say so. We never fabricate a deployment URL.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import structlog

from openmw.openvault.ship.hosts.base import DeployResult, DomainResult, Preflight

log = structlog.get_logger()

API_ROOT = "https://api.cloudflare.com/client/v4"

#: Cloudflare rejects project names outside this shape, and the error it
#: returns is opaque, so we normalise before sending rather than after failing.
_SAFE_NAME = re.compile(r"[^a-z0-9-]+")

#: Deploys upload every asset; a large site on a slow link needs real headroom.
_DEPLOY_TIMEOUT_S = 900.0
_API_TIMEOUT_S = 30.0


def normalize_project_name(raw: str) -> str:
    """Cloudflare project names: lowercase alphanumerics and dashes, <=58 chars."""
    name = _SAFE_NAME.sub("-", raw.strip().lower()).strip("-")
    name = re.sub(r"-{2,}", "-", name)
    if not name:
        name = "openvault-app"
    if not name[0].isalnum():
        name = f"a{name}"
    return name[:58].rstrip("-")


class CloudflarePagesAdapter:
    """Publish a built directory to the user's own Cloudflare account."""

    id = "cloudflare_pages"
    name = "Cloudflare Pages"
    credential_provider = "cloudflare"

    def __init__(self, *, api_token: str | None, account_id: str | None) -> None:
        self._token = (api_token or "").strip()
        self._account = (account_id or "").strip()

    # ── plumbing ────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _api(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any], str]:
        """Call the Cloudflare API. Returns (ok, result, human-readable error).

        Cloudflare answers 200 with ``success: false`` for domain-level
        refusals, so the HTTP status alone is not the outcome.
        """
        url = f"{API_ROOT}{path}"
        try:
            resp = httpx.request(
                method,
                url,
                headers=self._headers(),
                json=payload,
                timeout=_API_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            return False, {}, f"could not reach Cloudflare: {exc}"

        try:
            body = resp.json()
        except ValueError:
            return False, {}, f"Cloudflare returned non-JSON (HTTP {resp.status_code})"

        if not body.get("success", False):
            errors = body.get("errors") or []
            first = errors[0] if errors else {}
            message = first.get("message") or f"HTTP {resp.status_code}"
            code = first.get("code")
            return False, body, f"{message}{f' (code {code})' if code else ''}"
        return True, body.get("result") or {}, ""

    # ── HostAdapter ─────────────────────────────────────────────────────

    def preflight(self) -> Preflight:
        if not self._token:
            return Preflight(
                ready=False,
                blocker=(
                    "Add a Cloudflare API token to the vault. Create one at "
                    "dash.cloudflare.com/profile/api-tokens with the "
                    "'Cloudflare Pages: Edit' permission."
                ),
            )
        if not self._account:
            return Preflight(
                ready=False,
                blocker=(
                    "Set your Cloudflare account id (dash.cloudflare.com → any "
                    "domain → Overview → Account ID)."
                ),
            )

        ok, result, err = self._api("GET", "/user/tokens/verify")
        if not ok:
            return Preflight(ready=False, blocker=f"Cloudflare rejected the token: {err}")

        facts = {"token_status": str(result.get("status", "unknown"))}
        wrangler = shutil.which("wrangler") or shutil.which("wrangler.cmd")
        if wrangler:
            facts["wrangler"] = wrangler
        else:
            # Not fatal to *report*, but it is fatal to deploying, so say it now
            # rather than after a five-minute build.
            return Preflight(
                ready=False,
                blocker=(
                    "wrangler is not installed. Run: npm install -g wrangler "
                    "(Cloudflare's uploader — OpenVault shells out to it so "
                    "asset uploads stay on the supported path)."
                ),
                facts=facts,
            )
        return Preflight(ready=True, facts=facts)

    def ensure_project(self, project: str, *, production_branch: str = "main") -> tuple[bool, str]:
        """Create the Pages project if it does not exist. Idempotent."""
        name = normalize_project_name(project)
        ok, _, _ = self._api("GET", f"/accounts/{self._account}/pages/projects/{name}")
        if ok:
            return True, name

        ok, _, err = self._api(
            "POST",
            f"/accounts/{self._account}/pages/projects",
            payload={"name": name, "production_branch": production_branch},
        )
        if not ok:
            return False, err
        return True, name

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

        created, name_or_err = self.ensure_project(project)
        if not created:
            return DeployResult(ok=False, detail=f"could not create the Pages project: {name_or_err}")
        name = name_or_err

        wrangler = shutil.which("wrangler") or shutil.which("wrangler.cmd")
        if wrangler is None:  # pragma: no cover — preflight already refused
            return DeployResult(ok=False, detail="wrangler disappeared between preflight and deploy")

        env = os.environ.copy()
        env["CLOUDFLARE_API_TOKEN"] = self._token
        env["CLOUDFLARE_ACCOUNT_ID"] = self._account
        # Wrangler phones home and prompts on first run; both would hang a
        # non-interactive deploy.
        env["WRANGLER_SEND_METRICS"] = "false"
        env["CI"] = "true"

        cmd = [
            wrangler,
            "pages",
            "deploy",
            str(artifact),
            f"--project-name={name}",
            "--commit-dirty=true",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=_DEPLOY_TIMEOUT_S,
                check=False,
                env=env,
                cwd=str(artifact),
            )
        except subprocess.TimeoutExpired:
            return DeployResult(
                ok=False,
                detail=f"upload timed out after {int(_DEPLOY_TIMEOUT_S / 60)} minutes",
            )
        except OSError as exc:
            return DeployResult(ok=False, detail=f"could not run wrangler: {exc}")

        out = (proc.stdout or b"").decode(errors="replace")
        err = (proc.stderr or b"").decode(errors="replace")
        combined = f"{out}\n{err}".strip()

        if proc.returncode != 0:
            return DeployResult(
                ok=False,
                detail=f"wrangler exited {proc.returncode}",
                log=combined,
            )

        # Wrangler prints the deployment URL; parse rather than construct it,
        # because the preview subdomain is generated per deployment and a URL
        # we guessed could 404 while claiming success.
        match = re.search(r"https://[a-z0-9.-]*\.pages\.dev\S*", combined)
        url = match.group(0).rstrip(".,") if match else ""
        if not url:
            return DeployResult(
                ok=False,
                detail=(
                    "upload reported success but no pages.dev URL appeared in the "
                    "output — treating that as a failure rather than guessing a URL"
                ),
                log=combined,
            )

        log.info("cloudflare_pages_deployed", project=name, url=url)
        return DeployResult(ok=True, url=url, deployment_ref=name, detail=f"Live at {url}", log=combined)

    def attach_domain(self, *, project: str, hostname: str) -> DomainResult:
        """Register a custom domain on the Pages project.

        Cloudflare creates the DNS itself when the zone is in the same account.
        For a domain registered elsewhere (Spaceship, Namecheap, …) it cannot,
        so we return the exact CNAME to paste instead of failing silently.
        """
        host = hostname.strip().lower().rstrip(".")
        if not host:
            return DomainResult(ok=False, detail="no hostname given")

        name = normalize_project_name(project)
        ok, _, err = self._api(
            "POST",
            f"/accounts/{self._account}/pages/projects/{name}/domains",
            payload={"name": host},
        )
        if ok:
            return DomainResult(
                ok=True,
                hostname=host,
                detail=(
                    f"{host} attached. If the zone is on this Cloudflare account the "
                    "DNS record was created automatically; certificates take a few "
                    "minutes to issue."
                ),
            )

        # Already attached is a success from the user's point of view.
        if "already" in err.lower():
            return DomainResult(ok=True, hostname=host, detail=f"{host} was already attached")

        return DomainResult(
            ok=False,
            hostname=host,
            detail=(
                f"Cloudflare would not attach {host} automatically ({err}). "
                "This is normal when the domain is registered elsewhere — add "
                "the record below at your registrar, then retry."
            ),
            required_records=[
                {
                    "type": "CNAME",
                    "name": host,
                    "value": f"{name}.pages.dev",
                    "note": "proxied/orange-cloud if the zone is on Cloudflare",
                }
            ],
        )


def from_vault(get_secret_for_provider, account_id: str | None = None) -> CloudflarePagesAdapter:
    """Build the adapter from whatever the vault holds for `cloudflare`.

    Keeping credential lookup behind a callable means this module never
    imports the vault and stays unit-testable without a database.
    """
    token = get_secret_for_provider("cloudflare")
    return CloudflarePagesAdapter(
        api_token=token,
        account_id=account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID"),
    )


def parse_wrangler_url(output: str) -> str:
    """Exposed for tests: the URL-extraction rule the deploy path depends on."""
    match = re.search(r"https://[a-z0-9.-]*\.pages\.dev\S*", output)
    return match.group(0).rstrip(".,") if match else ""


__all__ = [
    "CloudflarePagesAdapter",
    "from_vault",
    "normalize_project_name",
    "parse_wrangler_url",
]
