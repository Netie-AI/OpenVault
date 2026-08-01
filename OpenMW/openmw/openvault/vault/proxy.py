"""OpenAI-compatible chat proxy with OpenVault fallback chain."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from openmw.openvault.route.attempt import AttemptOutcome, classify_attempt
from openmw.openvault.route.breaker import get_circuit_breaker
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.precheck import _default_base_url
from openmw.openvault.vault.providers import budget_for, resolve_model
from openmw.openvault.vault.store import KeyVault

log = structlog.get_logger()


def _is_multimodal(body: dict[str, Any]) -> bool:
    """True when any message carries an image part.

    OpenAI-shaped multimodal messages use a list of parts with `type: image_url`
    instead of a plain string. Routing one of those to a text-only model does not
    error - the image is simply ignored and the model answers about nothing, which
    is worse than refusing the hop.
    """
    for msg in body.get("messages") or ():
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("image_url", "input_image", "image"):
                return True
    return False


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

            # Translate the model per hop. Forwarding the caller's value verbatim sent
            # `"auto"` upstream as a model name, and every provider answered 404 - a
            # healthy key that read as a dead provider.
            wants_images = _is_multimodal(body)
            model = resolve_model(record.provider, body.get("model"), multimodal=wants_images)
            if model is None:
                why = "no vision model" if wants_images else "no catalogued model"
                errors.append(f"{record.label}: {why} for provider {record.provider}")
                continue
            hop_body = {**body, "model": model}
            raised = budget_for(record.provider, model, hop_body.get("max_tokens"))
            if raised is not None:
                # Never silently: a caller that asked for 32 and is billed for 512
                # deserves to see why in the log.
                log.info(
                    "openvault_reasoning_budget_raised",
                    provider=record.provider,
                    model=model,
                    requested=hop_body.get("max_tokens"),
                    sent=raised,
                )
                hop_body["max_tokens"] = raised

            try:
                resp = await client.post(url, headers=headers, json=hop_body)
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
                    key_ref=record.id[:8],
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


async def prepare_chat_stream(
    vault: KeyVault,
    fallback: FallbackManager,
    body: dict[str, Any],
    *,
    timeout_s: float = 120.0,
) -> tuple[int, dict[str, Any] | AsyncIterator[bytes]]:
    """Open a streaming upstream hop before returning bytes to the client.

    On success returns ``(status, async_iterator[bytes])``. On failure returns
    ``(status, error_payload)`` so the gateway can still emit a JSON error
    with the correct HTTP status (streaming cannot change status mid-flight).
    """
    candidates = fallback.ordered_candidates()
    if not candidates:
        return 503, {
            "error": {
                "message": "no healthy API keys in OpenVault fallback pool",
                "type": "openvault_no_keys",
            }
        }

    payload = dict(body)
    payload["stream"] = True
    errors: list[str] = []
    client = httpx.AsyncClient(timeout=timeout_s)

    async def _close_client() -> None:
        await client.aclose()

    try:
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

            if record.provider == "anthropic":
                errors.append(f"{record.label}: anthropic chat not via /v1 proxy yet")
                continue

            url = f"{base}/chat/completions"
            headers = {
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            }

            # Same per-hop translation as the non-streaming path. Fixing only one of
            # the two left streaming answering 404 while plain chat worked.
            wants_images = _is_multimodal(payload)
            model = resolve_model(record.provider, payload.get("model"), multimodal=wants_images)
            if model is None:
                why = "no vision model" if wants_images else "no catalogued model"
                errors.append(f"{record.label}: {why} for provider {record.provider}")
                continue
            hop_payload = {**payload, "model": model}
            raised = budget_for(record.provider, model, hop_payload.get("max_tokens"))
            if raised is not None:
                log.info(
                    "openvault_reasoning_budget_raised",
                    provider=record.provider,
                    model=model,
                    requested=hop_payload.get("max_tokens"),
                    sent=raised,
                )
                hop_payload["max_tokens"] = raised

            try:
                req = client.build_request("POST", url, headers=headers, json=hop_payload)
                resp = await client.send(req, stream=True)
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

            if resp.status_code >= 400:
                err_bytes = await resp.aread()
                await resp.aclose()
                body_text = err_bytes.decode("utf-8", errors="replace")
                outcome = classify_attempt(
                    resp.status_code, body_text, headers=dict(resp.headers)
                )
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
                    await _close_client()
                    return 400, {
                        "error": {
                            "message": "request rejected by upstream (non-retryable)",
                            "type": "openvault_non_retryable",
                            "reason": outcome.reason,
                            "details": errors,
                        }
                    }
                continue

            _apply_outcome(
                vault,
                fallback,
                key_id=record.id,
                provider=record.provider,
                outcome=classify_attempt(resp.status_code, "", headers=dict(resp.headers)),
                error="",
            )
            log.info(
                "openvault_proxy_stream_ok",
                key_ref=record.id[:8],
                provider=record.provider,
                role=record.role,
            )

            async def _byte_iter(
                response: httpx.Response = resp,
            ) -> AsyncIterator[bytes]:
                try:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk
                finally:
                    await response.aclose()
                    await _close_client()

            return resp.status_code, _byte_iter()
    except Exception:
        await _close_client()
        raise

    await _close_client()
    return 502, {
        "error": {
            "message": "all OpenVault fallback hops failed",
            "type": "openvault_fallback_exhausted",
            "details": errors,
        }
    }
