"""OpenAI-compatible chat proxy with OpenVault fallback chain."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from openmw.openvault.route.attempt import AttemptOutcome, classify_attempt
from openmw.openvault.route.breaker import get_circuit_breaker
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.precheck import _default_base_url
from openmw.openvault.vault.store import KeyVault

log = structlog.get_logger()


def _apply_outcome(
    vault: KeyVault,
    fallback: FallbackManager,
    *,
    key_id: str,
    provider: str,
    outcome: AttemptOutcome,
    error: str,
) -> None:
    """Mutate hop / provider health according to the attempt policy."""
    if outcome.attempt_class == "success":
        fallback.record_success(key_id)
        get_circuit_breaker(provider).record_success()
        return

    if outcome.candidate == "park":
        fallback.record_park(key_id, outcome.cooldown_ms, outcome.reason or error)
        return

    if outcome.candidate == "quarantine_key":
        vault.set_precheck(key_id, status="auth_fail", latency_ms=None, error=error)
        return

    if outcome.candidate == "eject_for_job":
        # Skip this key for this request only — no health mutation.
        return

    if outcome.counts_as_hard_fail:
        fallback.record_failure(key_id, error)
        if outcome.trip_provider_breaker:
            # Prefer status-aware trip when the error encodes HTTP NNN.
            status: int | None = None
            if error.startswith("HTTP "):
                try:
                    status = int(error.split()[1])
                except (IndexError, ValueError):
                    status = None
            get_circuit_breaker(provider).record_failure(status=status)


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
            breaker = get_circuit_breaker(record.provider)
            if not breaker.acquire_probe_slot():
                errors.append(f"{record.label}: provider circuit open")
                continue

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
                errors.append(f"{record.label}: anthropic chat not via /v1 proxy yet")
                continue

            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.TimeoutException:
                outcome = classify_attempt(None, "timeout")
                _apply_outcome(
                    vault,
                    fallback,
                    key_id=record.id,
                    provider=record.provider,
                    outcome=outcome,
                    error="timeout",
                )
                errors.append(f"{record.label}: timeout")
                continue
            except httpx.HTTPError as exc:
                outcome = classify_attempt(None, str(exc))
                _apply_outcome(
                    vault,
                    fallback,
                    key_id=record.id,
                    provider=record.provider,
                    outcome=outcome,
                    error=str(exc),
                )
                errors.append(f"{record.label}: {exc}")
                continue

            body_text = resp.text
            outcome = classify_attempt(resp.status_code, body_text, headers=dict(resp.headers))

            if outcome.attempt_class == "success":
                _apply_outcome(
                    vault,
                    fallback,
                    key_id=record.id,
                    provider=record.provider,
                    outcome=outcome,
                    error="",
                )
                log.info(
                    "openvault_proxy_ok",
                    key_id=record.id,
                    provider=record.provider,
                    role=record.role,
                )
                try:
                    return resp.status_code, resp.json()
                except Exception:
                    return resp.status_code, {"raw": body_text}

            err = f"HTTP {resp.status_code}"
            _apply_outcome(
                vault,
                fallback,
                key_id=record.id,
                provider=record.provider,
                outcome=outcome,
                error=err,
            )
            errors.append(f"{record.label}: {err} ({outcome.attempt_class})")

            if outcome.job == "dead":
                return 400, {
                    "error": {
                        "message": "request rejected by upstream (non-retryable)",
                        "type": "openvault_non_retryable",
                        "reason": outcome.reason,
                        "details": errors,
                    }
                }
            continue

    return 502, {
        "error": {
            "message": "all OpenVault fallback hops failed",
            "type": "openvault_fallback_exhausted",
            "details": errors,
        }
    }
