"""OpenAI-compatible chat proxy with OpenVault fallback chain."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from openmw.openvault.route.attempt import AttemptOutcome, classify_attempt
from openmw.openvault.route.breaker import get_circuit_breaker
from openmw.openvault.vault.budget import estimate_tokens_for_body, prepare_hop_body
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.precheck import _default_base_url
from openmw.openvault.vault.providers import resolve_model
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.usage_store import HopTrace

log = structlog.get_logger()

_SEALED_MESSAGE = "vault is sealed; POST /api/vault/unseal with the passphrase first"


def _sealed_refusal() -> tuple[int, dict[str, Any]]:
    """Typed FreeRoute refusal when decrypt is impossible (never HTTP 500)."""
    return 403, {
        "error": {
            "message": _SEALED_MESSAGE,
            "type": "openvault_vault_sealed",
        }
    }


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


def _no_candidates_refusal(vault: KeyVault) -> dict[str, Any]:
    """Say which kind of empty pool this is.

    "no healthy API keys" is a lie when the vault is holding keys it simply may
    not spend: since #36 the gateway walks pooled keys only, so an operator who
    has uploaded nothing but tenant-custody keys would otherwise be told their
    vault was empty and go looking in the wrong place (R-0011).
    """
    try:
        held = [k for k in vault.enabled_ordered() if k.custody != "pooled"]
    except Exception:  # pragma: no cover - a sealed or unreadable vault
        held = []
    if held:
        return {
            "error": {
                "message": (
                    "no pooled OpenVault key is available to serve this request; "
                    f"{len(held)} enabled key(s) are tenant-custody and are never "
                    "spent by the metered gateway"
                ),
                "type": "openvault_no_pooled_keys",
            }
        }
    return {
        "error": {
            "message": "no healthy API keys in OpenVault fallback pool",
            "type": "openvault_no_keys",
        }
    }


def affinity_key_for(body: dict[str, Any], *, tenant: str = "") -> str:
    """Stable identifier for this conversation, or "" when there is nothing to pin.

    Upstream prompt caches key on an exact prefix, so keeping a conversation on
    one account is worth real money — cached input runs about a tenth of list
    price.

    The key is the **fixed head** of the conversation: the leading system
    messages plus the first user turn. An earlier version hashed
    ``messages[:-1]``, which grows by two messages every turn — so turn 3 and
    turn 4 produced different keys and the conversation hopped accounts anyway,
    which is the exact thing this is supposed to prevent. The head is the part
    that is byte-identical on every turn, which is also the part the upstream
    cache actually matches on.

    Returns "" for a single-turn request: pinning one-shot traffic would
    concentrate unrelated calls onto one key and trade a cache we would have
    missed for a rate limit we would hit.
    """
    explicit = body.get("prompt_cache_key")
    if isinstance(explicit, str) and explicit.strip():
        # Namespaced by tenant: two callers who both send "default" must not
        # land on the same vault key just because they picked the same string.
        return f"{tenant}\x00{explicit.strip()[:4096]}"

    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return ""

    head: list[Any] = []
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else None
        head.append(message)
        if role not in ("system", "developer"):
            # Stop at the first non-system turn: system prompt + first user
            # message is the stable prefix every later turn repeats.
            break

    # json.dumps rather than an f-string join: "user:a\nuser:b" from two
    # messages and a single message whose content is "a\nuser:b" produced
    # byte-identical input before, so different conversations shared a hop.
    payload = json.dumps(head, sort_keys=True, default=str)
    if not _has_content(head):
        return ""
    return hashlib.sha256(f"{tenant}\x00{payload}".encode()).hexdigest()


def _has_content(messages: list[Any]) -> bool:
    """True when any message carries actual content, not just a role label."""
    for message in messages:
        if not isinstance(message, dict):
            return True
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return True
        if isinstance(content, list) and content:
            return True
        if content not in (None, "", [], {}):
            return True
    return False


def _context_refusal(errors: list[str]) -> tuple[int, dict[str, Any]]:
    """No hop could fit this prompt — say so without spending a single call."""
    return 400, {
        "error": {
            "message": (
                "prompt is longer than the context window of every model OpenVault "
                "could route it to"
            ),
            "type": "openvault_context_length_exceeded",
            "details": errors,
        }
    }


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
    trace: HopTrace | None = None,
    tenant: str = "",
) -> tuple[int, dict[str, Any] | str]:
    """Try each healthy hop until one succeeds.

    Returns ``(status_code, payload)``. Pass ``trace`` to learn which hop
    actually served — the usage ledger cannot attribute spend without it, and
    the return tuple is unpacked by four test modules that do not want it.
    """
    trace = trace if trace is not None else HopTrace()
    candidates = fallback.ordered_candidates(affinity_key=affinity_key_for(body, tenant=tenant))
    if not candidates:
        return 503, _no_candidates_refusal(vault)

    # Fail closed before hop walk: metadata may still be listed while sealed.
    # Walking then decrypting yields VaultSealedError -> dishonest 500 / exhausted.
    if vault.seal.is_sealed:
        log.warning("freeroute_refused", reason="vault_sealed", path="chat_completions")
        trace.error_type = "openvault_vault_sealed"
        return _sealed_refusal()

    errors: list[str] = []
    prompt_estimate = estimate_tokens_for_body(body)
    context_blocked = 0
    considered = 0
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for record in candidates:
            considered += 1
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
            decision = prepare_hop_body(
                body,
                provider=record.provider,
                model=model,
                prompt_tokens=prompt_estimate,
            )
            if decision.body is None:
                # Not a hop failure — this model simply cannot hold the prompt,
                # so it must not count against the key's health.
                if decision.context_exceeded:
                    context_blocked += 1
                errors.append(f"{record.label}: {decision.refusal}")
                continue
            hop_body = decision.body
            if decision.raised_to is not None:
                # Never silently: a caller that asked for 32 and is billed for 512
                # deserves to see why in the log.
                log.info(
                    "openvault_reasoning_budget_raised",
                    provider=record.provider,
                    model=model,
                    requested=body.get("max_tokens"),
                    sent=decision.raised_to,
                )
            if decision.clamped_to is not None:
                log.info(
                    "openvault_output_budget_clamped",
                    provider=record.provider,
                    model=model,
                    requested=body.get("max_tokens"),
                    sent=decision.clamped_to,
                )

            trace.note_attempt()
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
                trace.note_served(
                    provider=record.provider, model=model, vault_key_id=record.id
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
                trace.error_type = "openvault_non_retryable"
                return 400, {
                    "error": {
                        "message": "request rejected by upstream (non-retryable)",
                        "type": "openvault_non_retryable",
                        "reason": outcome.reason,
                        "details": errors,
                    }
                }
            continue

    if context_blocked and context_blocked == considered:
        # Every candidate was refused for size and nothing was spent. Saying
        # "all hops failed" here would blame the pool for the caller's prompt.
        trace.error_type = "openvault_context_length_exceeded"
        return _context_refusal(errors)

    trace.error_type = "openvault_fallback_exhausted"
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
    trace: HopTrace | None = None,
    tenant: str = "",
) -> tuple[int, dict[str, Any] | AsyncIterator[bytes]]:
    """Open a streaming upstream hop before returning bytes to the client.

    On success returns ``(status, async_iterator[bytes])``. On failure returns
    ``(status, error_payload)`` so the gateway can still emit a JSON error
    with the correct HTTP status (streaming cannot change status mid-flight).
    """
    trace = trace if trace is not None else HopTrace()
    candidates = fallback.ordered_candidates(affinity_key=affinity_key_for(body, tenant=tenant))
    if not candidates:
        return 503, _no_candidates_refusal(vault)

    if vault.seal.is_sealed:
        log.warning("freeroute_refused", reason="vault_sealed", path="prepare_chat_stream")
        trace.error_type = "openvault_vault_sealed"
        return _sealed_refusal()

    payload = dict(body)
    payload["stream"] = True
    errors: list[str] = []
    prompt_estimate = estimate_tokens_for_body(body)
    context_blocked = 0
    considered = 0
    client = httpx.AsyncClient(timeout=timeout_s)

    async def _close_client() -> None:
        await client.aclose()

    try:
        for record in candidates:
            considered += 1
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
            decision = prepare_hop_body(
                payload,
                provider=record.provider,
                model=model,
                prompt_tokens=prompt_estimate,
            )
            if decision.body is None:
                if decision.context_exceeded:
                    context_blocked += 1
                errors.append(f"{record.label}: {decision.refusal}")
                continue
            hop_payload = decision.body
            hop_payload["stream"] = True
            if decision.raised_to is not None:
                log.info(
                    "openvault_reasoning_budget_raised",
                    provider=record.provider,
                    model=model,
                    requested=body.get("max_tokens"),
                    sent=decision.raised_to,
                )
            if decision.clamped_to is not None:
                log.info(
                    "openvault_output_budget_clamped",
                    provider=record.provider,
                    model=model,
                    requested=body.get("max_tokens"),
                    sent=decision.clamped_to,
                )

            trace.note_attempt()
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
                    trace.error_type = "openvault_non_retryable"
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
            trace.note_served(provider=record.provider, model=model, vault_key_id=record.id)

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
    if context_blocked and context_blocked == considered:
        trace.error_type = "openvault_context_length_exceeded"
        return _context_refusal(errors)

    trace.error_type = "openvault_fallback_exhausted"
    return 502, {
        "error": {
            "message": "all OpenVault fallback hops failed",
            "type": "openvault_fallback_exhausted",
            "details": errors,
        }
    }
