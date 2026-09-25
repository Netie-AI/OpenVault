"""OpenAI-compatible chat proxy with OpenVault fallback chain."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from openmw.openvault.route.attempt import AttemptOutcome, classify_attempt
from openmw.openvault.route.breaker import get_circuit_breaker
from openmw.openvault.vault.budget import estimate_tokens_for_body, prepare_hop_body
from openmw.openvault.vault.crypto import VaultCryptoError, VaultSealedError
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.local_hop import (
    LOCAL_HOP_KEY_ID,
    LOCAL_PLACEHOLDER_SECRET,
    REASON_MODEL_NOT_LOADED,
    REASON_UNREACHABLE,
    attach_served_fields,
    chat_error_is_model_missing,
    inject_served_into_sse_chunk,
    local_only_refusal,
    pop_local_only,
    probe_local,
    walkable_local_base,
)
from openmw.openvault.vault.precheck import _default_base_url
from openmw.openvault.vault.providers import LOCAL_QWEN_ID, get_provider, resolve_model
from openmw.openvault.vault.store import KeyRecord, KeyVault
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


def _compat_skip(label: str, provider: str) -> str | None:
    """Why this hop cannot go through the OpenAI-compat /v1 spend path."""
    if provider == "anthropic":
        return f"{label}: anthropic chat not via /v1 proxy yet"
    spec = get_provider(provider)
    if spec is not None and not spec.openai_compatible:
        return f"{label}: {spec.name} is not on the OpenAI-compat /v1 spend path"
    return None


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


@dataclass(frozen=True)
class ProxyCandidate:
    """One hop the proxy may contact. Local hops have no vault KeyRecord."""

    label: str
    provider: str
    base_url: str
    role: str
    key_id: str
    served_local: bool
    record: KeyRecord | None
    secret: str | None = None


def _local_candidate(base: str) -> ProxyCandidate:
    spec = get_provider(LOCAL_QWEN_ID)
    return ProxyCandidate(
        label=spec.name if spec is not None else "Local Qwen (loopback)",
        provider=LOCAL_QWEN_ID,
        base_url=base,
        role="free",
        key_id=LOCAL_HOP_KEY_ID,
        served_local=True,
        record=None,
        secret=LOCAL_PLACEHOLDER_SECRET,
    )


def _vault_candidates(
    fallback: FallbackManager, body: dict[str, Any], *, tenant: str
) -> list[ProxyCandidate]:
    hops: list[ProxyCandidate] = []
    for record in fallback.ordered_candidates(affinity_key=affinity_key_for(body, tenant=tenant)):
        hops.append(
            ProxyCandidate(
                label=record.label,
                provider=record.provider,
                base_url=record.base_url,
                role=record.role,
                key_id=record.id,
                served_local=False,
                record=record,
            )
        )
    return hops


def _collect_candidates(
    vault: KeyVault,
    fallback: FallbackManager,
    body: dict[str, Any],
    *,
    tenant: str,
    local_only: bool,
) -> tuple[list[ProxyCandidate], tuple[int, dict[str, Any]] | None]:
    """Build the hop list. local_only never returns a cloud hop."""
    base, leftover = walkable_local_base()
    if local_only:
        if base is None:
            return [], local_only_refusal(leftover)
        return [_local_candidate(base)], None

    hops: list[ProxyCandidate] = []
    if base is not None and probe_local().ok:
        hops.append(_local_candidate(base))
    hops.extend(_vault_candidates(fallback, body, tenant=tenant))
    if not hops:
        return [], (503, _no_candidates_refusal(vault))
    return hops, None


def _secret_for_candidate(vault: KeyVault, cand: ProxyCandidate, errors: list[str]) -> str | None:
    if cand.secret is not None:
        return cand.secret
    if cand.record is None:
        return None
    return _secret_for_hop(vault, cand.record, errors)


def _apply_candidate_outcome(
    vault: KeyVault,
    fallback: FallbackManager,
    cand: ProxyCandidate,
    outcome: AttemptOutcome,
    error: str,
) -> None:
    if cand.served_local:
        breaker = get_circuit_breaker(cand.provider)
        if outcome.attempt_class == "success":
            breaker.record_success()
            return
        if outcome.counts_as_hard_fail and outcome.trip_provider_breaker:
            status: int | None = None
            if error.startswith("HTTP "):
                try:
                    status = int(error.split()[1])
                except (IndexError, ValueError):
                    status = None
            breaker.record_failure(status=status)
        return
    _apply_outcome(
        vault,
        fallback,
        key_id=cand.key_id,
        provider=cand.provider,
        outcome=outcome,
        error=error,
    )


def _stamp_served(
    payload: dict[str, Any] | str,
    cand: ProxyCandidate,
    model: str,
) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {"raw": payload}
    return attach_served_fields(
        body, provider=cand.provider, model=model, served_local=cand.served_local
    )


def _local_fail_reason(status: int | None, body_text: str, model: str) -> str:
    if status is not None and chat_error_is_model_missing(status, body_text, model):
        return REASON_MODEL_NOT_LOADED
    return REASON_UNREACHABLE


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


def _secret_for_hop(vault: KeyVault, record: KeyRecord, errors: list[str]) -> str | None:
    """Decrypt one hop secret. None means skip (never raise into a gateway 500)."""
    try:
        return vault.get_secret(record.id)
    except VaultSealedError:
        raise
    except VaultCryptoError:
        log.warning(
            "openvault_hop_decrypt_failed",
            key_ref=record.id[:8],
            provider=record.provider,
        )
        errors.append(f"{record.label}: decrypt failed")
        return None


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
    work = dict(body)
    local_only = pop_local_only(work)
    candidates, early = _collect_candidates(
        vault, fallback, work, tenant=tenant, local_only=local_only
    )
    if early is not None:
        err = early[1].get("error")
        if isinstance(err, dict) and err.get("type"):
            trace.error_type = str(err["type"])
        return early[0], early[1]

    # Fail closed before hop walk: metadata may still be listed while sealed.
    # Walking then decrypting yields VaultSealedError -> dishonest 500 / exhausted.
    if vault.seal.is_sealed:
        log.warning("freeroute_refused", reason="vault_sealed", path="chat_completions")
        trace.error_type = "openvault_vault_sealed"
        return _sealed_refusal()

    errors: list[str] = []
    prompt_estimate = estimate_tokens_for_body(work)
    context_blocked = 0
    considered = 0
    local_fail_reason = REASON_UNREACHABLE
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for cand in candidates:
            considered += 1
            breaker = get_circuit_breaker(cand.provider)
            if not breaker.acquire_probe_slot():
                errors.append(f"{cand.label}: provider circuit open")
                continue

            try:
                secret = _secret_for_candidate(vault, cand, errors)
            except VaultSealedError:
                trace.error_type = "openvault_vault_sealed"
                return _sealed_refusal()
            if secret is None:
                continue
            base = (
                cand.base_url.rstrip("/")
                if cand.served_local
                else _default_base_url(cand.provider, cand.base_url)
            )
            if not base:
                if not cand.served_local:
                    fallback.record_failure(cand.key_id, "missing base_url")
                errors.append(f"{cand.label}: missing base_url")
                continue

            url = f"{base}/chat/completions"
            headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
            skip = _compat_skip(cand.label, cand.provider)
            if skip is not None:
                errors.append(skip)
                continue

            # Translate the model per hop. Forwarding the caller's value verbatim sent
            # `"auto"` upstream as a model name, and every provider answered 404 - a
            # healthy key that read as a dead provider.
            wants_images = _is_multimodal(work)
            model = resolve_model(cand.provider, work.get("model"), multimodal=wants_images)
            if model is None:
                why = "no vision model" if wants_images else "no catalogued model"
                errors.append(f"{cand.label}: {why} for provider {cand.provider}")
                continue
            decision = prepare_hop_body(
                work,
                provider=cand.provider,
                model=model,
                prompt_tokens=prompt_estimate,
            )
            if decision.body is None:
                # Not a hop failure — this model simply cannot hold the prompt,
                # so it must not count against the key's health.
                if decision.context_exceeded:
                    context_blocked += 1
                errors.append(f"{cand.label}: {decision.refusal}")
                continue
            hop_body = decision.body
            if decision.raised_to is not None:
                # Never silently: a caller that asked for 32 and is billed for 512
                # deserves to see why in the log.
                log.info(
                    "openvault_reasoning_budget_raised",
                    provider=cand.provider,
                    model=model,
                    requested=work.get("max_tokens"),
                    sent=decision.raised_to,
                )
            if decision.clamped_to is not None:
                log.info(
                    "openvault_output_budget_clamped",
                    provider=cand.provider,
                    model=model,
                    requested=work.get("max_tokens"),
                    sent=decision.clamped_to,
                )

            trace.note_attempt()
            try:
                resp = await client.post(url, headers=headers, json=hop_body)
            except httpx.TimeoutException:
                outcome = classify_attempt(None, "timeout")
                _apply_candidate_outcome(vault, fallback, cand, outcome, "timeout")
                errors.append(f"{cand.label}: timeout")
                if cand.served_local:
                    local_fail_reason = REASON_UNREACHABLE
                continue
            except (httpx.HTTPError, OSError) as exc:
                outcome = classify_attempt(None, str(exc))
                _apply_candidate_outcome(vault, fallback, cand, outcome, str(exc))
                errors.append(f"{cand.label}: {exc}")
                if cand.served_local:
                    local_fail_reason = REASON_UNREACHABLE
                continue

            body_text = resp.text
            outcome = classify_attempt(resp.status_code, body_text, headers=dict(resp.headers))

            if outcome.attempt_class == "success":
                _apply_candidate_outcome(vault, fallback, cand, outcome, "")
                log.info(
                    "openvault_proxy_ok",
                    key_ref=cand.key_id[:8],
                    provider=cand.provider,
                    role=cand.role,
                )
                trace.note_served(
                    provider=cand.provider,
                    model=model,
                    vault_key_id="" if cand.served_local else cand.key_id,
                    served_local=cand.served_local,
                )
                try:
                    payload: dict[str, Any] | str = resp.json()
                except Exception:
                    payload = {"raw": body_text}
                return resp.status_code, _stamp_served(payload, cand, model)

            err = f"HTTP {resp.status_code}"
            _apply_candidate_outcome(vault, fallback, cand, outcome, err)
            errors.append(f"{cand.label}: {err} ({outcome.attempt_class})")
            if cand.served_local:
                local_fail_reason = _local_fail_reason(resp.status_code, body_text, model)

            if outcome.job == "dead":
                if local_only:
                    trace.error_type = "openvault_local_only_unavailable"
                    return local_only_refusal(local_fail_reason)
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

    if local_only:
        trace.error_type = "openvault_local_only_unavailable"
        return local_only_refusal(local_fail_reason)

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
    work = dict(body)
    local_only = pop_local_only(work)
    candidates, early = _collect_candidates(
        vault, fallback, work, tenant=tenant, local_only=local_only
    )
    if early is not None:
        err = early[1].get("error")
        if isinstance(err, dict) and err.get("type"):
            trace.error_type = str(err["type"])
        return early[0], early[1]

    if vault.seal.is_sealed:
        log.warning("freeroute_refused", reason="vault_sealed", path="prepare_chat_stream")
        trace.error_type = "openvault_vault_sealed"
        return _sealed_refusal()

    payload = dict(work)
    payload["stream"] = True
    errors: list[str] = []
    prompt_estimate = estimate_tokens_for_body(work)
    context_blocked = 0
    considered = 0
    local_fail_reason = REASON_UNREACHABLE
    client = httpx.AsyncClient(timeout=timeout_s)

    async def _close_client() -> None:
        await client.aclose()

    try:
        for cand in candidates:
            considered += 1
            breaker = get_circuit_breaker(cand.provider)
            if not breaker.acquire_probe_slot():
                errors.append(f"{cand.label}: provider circuit open")
                continue

            try:
                secret = _secret_for_candidate(vault, cand, errors)
            except VaultSealedError:
                await _close_client()
                trace.error_type = "openvault_vault_sealed"
                return _sealed_refusal()
            if secret is None:
                continue
            base = (
                cand.base_url.rstrip("/")
                if cand.served_local
                else _default_base_url(cand.provider, cand.base_url)
            )
            if not base:
                if not cand.served_local:
                    fallback.record_failure(cand.key_id, "missing base_url")
                errors.append(f"{cand.label}: missing base_url")
                continue

            skip = _compat_skip(cand.label, cand.provider)
            if skip is not None:
                errors.append(skip)
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
            model = resolve_model(cand.provider, payload.get("model"), multimodal=wants_images)
            if model is None:
                why = "no vision model" if wants_images else "no catalogued model"
                errors.append(f"{cand.label}: {why} for provider {cand.provider}")
                continue
            decision = prepare_hop_body(
                payload,
                provider=cand.provider,
                model=model,
                prompt_tokens=prompt_estimate,
            )
            if decision.body is None:
                if decision.context_exceeded:
                    context_blocked += 1
                errors.append(f"{cand.label}: {decision.refusal}")
                continue
            hop_payload = decision.body
            hop_payload["stream"] = True
            if decision.raised_to is not None:
                log.info(
                    "openvault_reasoning_budget_raised",
                    provider=cand.provider,
                    model=model,
                    requested=work.get("max_tokens"),
                    sent=decision.raised_to,
                )
            if decision.clamped_to is not None:
                log.info(
                    "openvault_output_budget_clamped",
                    provider=cand.provider,
                    model=model,
                    requested=work.get("max_tokens"),
                    sent=decision.clamped_to,
                )

            trace.note_attempt()
            try:
                req = client.build_request("POST", url, headers=headers, json=hop_payload)
                resp = await client.send(req, stream=True)
            except httpx.TimeoutException:
                outcome = classify_attempt(None, "timeout")
                _apply_candidate_outcome(vault, fallback, cand, outcome, "timeout")
                errors.append(f"{cand.label}: timeout")
                if cand.served_local:
                    local_fail_reason = REASON_UNREACHABLE
                continue
            except (httpx.HTTPError, OSError) as exc:
                outcome = classify_attempt(None, str(exc))
                _apply_candidate_outcome(vault, fallback, cand, outcome, str(exc))
                errors.append(f"{cand.label}: {exc}")
                if cand.served_local:
                    local_fail_reason = REASON_UNREACHABLE
                continue

            if resp.status_code >= 400:
                err_bytes = await resp.aread()
                await resp.aclose()
                body_text = err_bytes.decode("utf-8", errors="replace")
                outcome = classify_attempt(resp.status_code, body_text, headers=dict(resp.headers))
                err = f"HTTP {resp.status_code}"
                _apply_candidate_outcome(vault, fallback, cand, outcome, err)
                errors.append(f"{cand.label}: {err} ({outcome.attempt_class})")
                if cand.served_local:
                    local_fail_reason = _local_fail_reason(resp.status_code, body_text, model)
                if outcome.job == "dead":
                    await _close_client()
                    if local_only:
                        trace.error_type = "openvault_local_only_unavailable"
                        return local_only_refusal(local_fail_reason)
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

            _apply_candidate_outcome(
                vault,
                fallback,
                cand,
                classify_attempt(resp.status_code, "", headers=dict(resp.headers)),
                "",
            )
            log.info(
                "openvault_proxy_stream_ok",
                key_ref=cand.key_id[:8],
                provider=cand.provider,
                role=cand.role,
            )
            trace.note_served(
                provider=cand.provider,
                model=model,
                vault_key_id="" if cand.served_local else cand.key_id,
                served_local=cand.served_local,
            )
            served_provider = cand.provider
            served_model = model
            served_local = cand.served_local

            async def _byte_iter(
                response: httpx.Response = resp,
                inj_provider: str = served_provider,
                inj_model: str = served_model,
                inj_local: bool = served_local,
            ) -> AsyncIterator[bytes]:
                try:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield inject_served_into_sse_chunk(
                                chunk,
                                provider=inj_provider,
                                model=inj_model,
                                served_local=inj_local,
                            )
                finally:
                    await response.aclose()
                    await _close_client()

            return resp.status_code, _byte_iter()
    except Exception:
        await _close_client()
        raise

    await _close_client()
    if local_only:
        trace.error_type = "openvault_local_only_unavailable"
        return local_only_refusal(local_fail_reason)
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
