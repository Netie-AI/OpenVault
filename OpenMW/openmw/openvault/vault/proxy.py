"""OpenAI-compatible chat proxy with OpenVault fallback chain."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.precheck import _default_base_url
from openmw.openvault.vault.store import KeyVault

log = structlog.get_logger()


async def chat_completions(
    vault: KeyVault,
    fallback: FallbackManager,
    body: dict[str, Any],
    *,
    timeout_s: float = 60.0,
) -> tuple[int, dict[str, Any] | str]:
    """Try each healthy hop until one succeeds.

    Returns ``(status_code, payload)``.
    """
    candidates = fallback.ordered_candidates()
    if not candidates:
        return 503, {
            "error": {
                "message": "no healthy API keys in OpenVault fallback pool",
                "type": "openvault_no_keys",
            }
        }

    errors: list[str] = []
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for record in candidates:
            secret = vault.get_secret(record.id)
            if secret is None:
                continue
            base = _default_base_url(record.provider, record.base_url)
            if not base:
                fallback.record_failure(record.id, "missing base_url")
                errors.append(f"{record.label}: missing base_url")
                continue

            url = f"{base}/chat/completions"
            headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
            if record.provider == "anthropic":
                # Anthropic uses a different API; skip OpenAI-shape for now
                errors.append(f"{record.label}: anthropic chat not via /v1 proxy yet")
                continue

            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.TimeoutException:
                fallback.record_failure(record.id, "timeout")
                errors.append(f"{record.label}: timeout")
                continue
            except httpx.HTTPError as exc:
                fallback.record_failure(record.id, str(exc))
                errors.append(f"{record.label}: {exc}")
                continue

            if resp.status_code >= 200 and resp.status_code < 300:
                fallback.record_success(record.id)
                log.info(
                    "openvault_proxy_ok",
                    key_id=record.id,
                    provider=record.provider,
                    role=record.role,
                )
                try:
                    return resp.status_code, resp.json()
                except Exception:
                    return resp.status_code, {"raw": resp.text}

            err = f"HTTP {resp.status_code}"
            fallback.record_failure(record.id, err)
            errors.append(f"{record.label}: {err}")
            # auth failures should not burn the whole chain silently — continue
            continue

    return 502, {
        "error": {
            "message": "all OpenVault fallback hops failed",
            "type": "openvault_fallback_exhausted",
            "details": errors,
        }
    }
