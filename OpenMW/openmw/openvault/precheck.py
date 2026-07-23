"""Continuous and on-demand API-key health prechecks."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import structlog

from openmw.openvault.vault import KeyRecord, KeyVault, PrecheckStatus

log = structlog.get_logger()

DEFAULT_PRECHECK_INTERVAL_S = 60.0
DEFAULT_TIMEOUT_S = 8.0


@dataclass(frozen=True)
class PrecheckResult:
    key_id: str
    status: PrecheckStatus
    latency_ms: float | None
    error: str | None


def _default_base_url(provider: str, base_url: str) -> str:
    if base_url.strip():
        return base_url.rstrip("/")
    from openmw.openvault.providers import get_provider

    spec = get_provider(provider)
    if spec is not None:
        return spec.base_url.rstrip("/")
    defaults = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "google": "https://generativelanguage.googleapis.com/v1beta",
        "mistral": "https://api.mistral.ai/v1",
        "huggingface": "https://huggingface.co",
        "cortex": "http://127.0.0.1:8000",
        "ollama": "http://127.0.0.1:11434/v1",
        "custom": "",
    }
    return defaults.get(provider, "").rstrip("/")


def classify_http_error(status_code: int, _body: str = "") -> PrecheckStatus:
    if status_code in (401, 403):
        return "auth_fail"
    if status_code == 429:
        return "rate_limit"
    if status_code >= 500:
        return "error"
    # 404 on models endpoint still proves auth often works for some providers
    if status_code == 404:
        return "ok"
    if 200 <= status_code < 300:
        return "ok"
    return "error"


async def probe_key(
    record: KeyRecord,
    secret: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> PrecheckResult:
    """Probe a provider key with a lightweight authenticated request."""
    base = _default_base_url(record.provider, record.base_url)
    if not base:
        return PrecheckResult(record.id, "error", None, "missing base_url for custom provider")

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout_s)
    started = time.perf_counter()
    try:
        if record.provider == "anthropic":
            url = f"{base}/v1/models"
            headers = {
                "x-api-key": secret,
                "anthropic-version": "2023-06-01",
            }
            resp = await http.get(url, headers=headers)
        elif record.provider == "cortex":
            resp = await http.get(f"{base}/health" if not base.endswith("/health") else base)
        elif record.provider == "ollama":
            # Prefer OpenAI-compatible /v1/models; fall back to native /api/tags
            try:
                resp = await http.get(f"{base}/models")
                if resp.status_code >= 500:
                    resp = await http.get("http://127.0.0.1:11434/api/tags")
            except httpx.HTTPError:
                resp = await http.get("http://127.0.0.1:11434/api/tags")
        elif record.provider == "huggingface":
            resp = await http.get(
                f"{base}/api/whoami-v2",
                headers={"Authorization": f"Bearer {secret}"},
            )
        else:
            # OpenAI-compatible models list
            resp = await http.get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {secret}"},
            )
        latency = (time.perf_counter() - started) * 1000.0
        status = classify_http_error(resp.status_code, resp.text[:200])
        err = None if status == "ok" else f"HTTP {resp.status_code}"
        return PrecheckResult(record.id, status, latency, err)
    except httpx.TimeoutException:
        return PrecheckResult(record.id, "timeout", None, "timeout")
    except httpx.HTTPError as exc:
        return PrecheckResult(record.id, "error", None, str(exc))
    finally:
        if owns_client:
            await http.aclose()


async def precheck_one(vault: KeyVault, key_id: str) -> PrecheckResult:
    record = vault.get(key_id)
    if record is None:
        return PrecheckResult(key_id, "error", None, "key not found")
    secret = vault.get_secret(key_id)
    if secret is None:
        return PrecheckResult(key_id, "error", None, "secret missing")
    result = await probe_key(record, secret)
    vault.set_precheck(
        key_id,
        status=result.status,
        latency_ms=result.latency_ms,
        error=result.error,
    )
    log.info(
        "openvault_precheck",
        key_id=key_id,
        status=result.status,
        latency_ms=result.latency_ms,
    )
    return result


async def precheck_all(vault: KeyVault) -> list[PrecheckResult]:
    results: list[PrecheckResult] = []
    for record in vault.list_keys():
        if not record.enabled:
            continue
        results.append(await precheck_one(vault, record.id))
    return results


class PrecheckLoop:
    """Background loop that constantly re-tests enabled keys."""

    def __init__(
        self,
        vault: KeyVault,
        *,
        interval_s: float = DEFAULT_PRECHECK_INTERVAL_S,
        sleeper: Callable[[float], object] | None = None,
    ) -> None:
        self._vault = vault
        self._interval_s = interval_s
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run_forever(self) -> None:
        import asyncio

        while not self._stop:
            try:
                await precheck_all(self._vault)
            except Exception as exc:
                log.warning("openvault_precheck_loop_error", error=str(exc))
            await asyncio.sleep(self._interval_s)
