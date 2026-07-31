"""HTTP client for real FreeBuild control plane (oblien/openship).

Wraps ``{OPENSHIP_URL}/api`` — do not invent CLI subcommands that do not exist.
When URL/token missing, callers should fall back to simulate + teach guides.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import structlog

log = structlog.get_logger()

DeployTarget = Literal["local", "server", "cloud"]
CloudTier = Literal["micro", "low", "medium", "high", "custom"]


@dataclass(frozen=True)
class OpenShipConfig:
    """Connection settings from env (OpenVault custody layer)."""

    base_url: str
    token: str
    org_id: str | None = None
    timeout_s: float = 120.0

    @classmethod
    def from_env(cls) -> OpenShipConfig | None:
        url = os.environ.get("OPENSHIP_URL", "").rstrip("/")
        token = os.environ.get("OPENSHIP_TOKEN", "").strip()
        if not url or not token:
            return None
        org = os.environ.get("OPENSHIP_ORG_ID", "").strip() or None
        timeout_raw = os.environ.get("OPENSHIP_TIMEOUT_S", "120")
        try:
            timeout_s = float(timeout_raw)
        except ValueError:
            timeout_s = 120.0
        return cls(base_url=url, token=token, org_id=org, timeout_s=timeout_s)

    def configured(self) -> bool:
        return bool(self.base_url and self.token)


class OpenShipClient:
    """Thin typed wrapper over FreeBuild REST (Render/Vercel-like ship engine)."""

    def __init__(self, config: OpenShipConfig | None = None) -> None:
        self.config = config if config is not None else OpenShipConfig.from_env()
        self._client: httpx.Client | None = None

    @property
    def available(self) -> bool:
        return self.config is not None and self.config.configured()

    def _headers(self) -> dict[str, str]:
        assert self.config is not None
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.config.org_id:
            headers["X-Organization-Id"] = self.config.org_id
        return headers

    def _http(self) -> httpx.Client:
        if self._client is None:
            assert self.config is not None
            self._client = httpx.Client(
                base_url=f"{self.config.base_url}/api",
                headers=self._headers(),
                timeout=self.config.timeout_s,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "error": "OPENSHIP_URL and OPENSHIP_TOKEN required"}
        try:
            resp = self._http().request(method, path, json=json_body)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("openship_http_error", path=path, error=str(exc))
            return {"ok": False, "error": str(exc)}
        try:
            data: Any = resp.json()
        except ValueError:
            data = {"raw": (resp.text or "")[:2000]}
        if not isinstance(data, dict):
            data = {"data": data}
        data.setdefault("ok", resp.is_success)
        data.setdefault("http_status", resp.status_code)
        if not resp.is_success and "error" not in data:
            data["error"] = f"HTTP {resp.status_code}"
        return data

    # --- presence / billing ---

    def cloud_status(self) -> dict[str, Any]:
        return self._request("GET", "/cloud/status")

    def billing_state(self) -> dict[str, Any]:
        return self._request("GET", "/billing/state")

    def billing_usage(self) -> dict[str, Any]:
        return self._request("GET", "/billing/usage")

    # --- github ---

    def github_home(self) -> dict[str, Any]:
        return self._request("GET", "/github/home")

    def github_repos(self) -> dict[str, Any]:
        return self._request("GET", "/github/repos")

    def github_connect(self) -> dict[str, Any]:
        return self._request("POST", "/github/connect", json_body={})

    # --- servers (any VPS / Hetzner SSH) ---

    def list_servers(self) -> dict[str, Any]:
        return self._request("GET", "/system/servers")

    def add_server(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/system/servers", json_body=body)

    def test_connection(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/system/test-connection", json_body=body)

    def install_server(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/system/install", json_body=body)

    # --- deploy ---

    def prepare(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/deployments/prepare", json_body=body)

    def ensure_project(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/projects/ensure", json_body=body)

    def build_access(self, body: dict[str, Any]) -> dict[str, Any]:
        """One-click Deploy — FreeBuild wizard equivalent of Deploy button."""
        return self._request("POST", "/deployments/build/access", json_body=body)

    def deployment_status(self, deployment_id: str) -> dict[str, Any]:
        return self._request("GET", f"/deployments/{deployment_id}")

    # --- domains ---

    def domain_preview(self, hostname: str) -> dict[str, Any]:
        return self._request("POST", "/domains/preview", json_body={"hostname": hostname})

    def add_domain(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/domains", json_body=body)

    def verify_domain(self, domain_id: str) -> dict[str, Any]:
        return self._request("POST", f"/domains/{domain_id}/verify", json_body={})


def adapter_status() -> dict[str, Any]:
    """Honest presence: API / CLI / simulate — what can actually ship."""
    import shutil

    cfg = OpenShipConfig.from_env()
    cli = os.environ.get("OPENSHIP_CLI", "openship")
    which = shutil.which(cli)
    mode = os.environ.get("OPENSHIP_MODE", "auto")
    api_ready = cfg is not None and cfg.configured()
    if mode == "simulate":
        effective = "simulate"
    elif api_ready:
        effective = "api"
    elif which:
        effective = "cli"
    elif mode == "auto":
        effective = "simulate"
    else:
        effective = mode
    return {
        "mode": mode,
        "effective": effective,
        "api_ready": api_ready,
        "api_url": cfg.base_url if cfg else None,
        "cli_found": which is not None,
        "cli_path": which,
        "vendor_tree": "D:\\OpenVault\\vendor\\openship",
        "docs": "https://openship.io/docs",
        "install_hint": "npm i -g openship  OR  set OPENSHIP_URL + OPENSHIP_TOKEN",
        "honest": (
            "FreeBuild ships via Oblien Cloud, SSH VPS (Hetzner/DO/any), or local Docker — "
            "not native AWS ELB/Lambda/S3 app provisioning. AWS path is teach+budget until "
            "OpenVault AWS adapter lands."
        ),
    }
