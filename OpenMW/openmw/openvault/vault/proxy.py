"""OpenAI-compatible chat proxy with OpenVault fallback chain."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import structlog

from openmw.openvault.route.attempt import AttemptOutcome, classify_attempt
from openmw.openvault.route.breaker import get_circuit_breaker
from openmw.openvault.vault.budget import BudgetDecision, estimate_tokens_for_body, prepare_hop_body
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
from openmw.openvault.vault.parks import provider_error_text
from openmw.openvault.vault.precheck import _default_base_url
from openmw.openvault.vault.providers import (
    LOCAL_QWEN_ID,
    catalog_contains_model,
    get_provider,
    models_for,
    resolve_model,
)
from openmw.openvault.vault.quota import iso_utc, quota_blocks, quota_retry_after_s
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


def _all_hops_parked_body(until: float) -> dict[str, Any]:
    return {
        "error": {
            "message": f"all hops parked, retry at {iso_utc(until)}",
            "type": "openvault_all_hops_parked",
        }
    }


def _parked_pool_refusal(
    vault: KeyVault, fallback: FallbackManager
) -> tuple[int, dict[str, Any], int] | None:
    """503 when every pooled hop is inside a park window."""
    pooled = list(vault.pooled_ordered())
    if not pooled or any(not fallback.key_is_parked(record.id) for record in pooled):
        return None
    until = fallback.soonest_key_park_until([record.id for record in pooled])
    if until is None:
        return None
    retry = max(1, math.ceil(until - time.time()))
    return 503, _all_hops_parked_body(until), retry


def _collect_candidates(
    vault: KeyVault,
    fallback: FallbackManager,
    body: dict[str, Any],
    *,
    tenant: str,
    local_only: bool,
) -> tuple[list[ProxyCandidate], tuple[int, dict[str, Any], int | None] | None]:
    """Build the hop list. local_only never returns a cloud hop."""
    base, leftover = walkable_local_base()
    if local_only:
        if base is None:
            status, body_out = local_only_refusal(leftover)
            return [], (status, body_out, None)
        return [_local_candidate(base)], None

    hops: list[ProxyCandidate] = []
    if base is not None and probe_local().ok:
        hops.append(_local_candidate(base))
    hops.extend(_vault_candidates(fallback, body, tenant=tenant))
    if not hops:
        parked = _parked_pool_refusal(vault, fallback)
        if parked is not None:
            return [], parked
        return [], (503, _no_candidates_refusal(vault), None)
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
    *,
    error_text: str = "",
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
        error_text=error_text,
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


_ModelStep = Literal["served", "dead", "next_model", "next_hop"]


def _models_for_hop(provider: str, requested: str | None, *, multimodal: bool) -> tuple[str, ...]:
    """Model ids this hop may send, strongest first.

    A pinned id the provider serves is the only id. ``auto``, or an id this
    provider does not serve, walks the catalog. That is the only in-provider
    fallback: a pinned model must never be swapped for a sibling here.
    """
    pool = models_for(provider, multimodal=multimodal)
    want = (requested or "").strip()
    resolved = resolve_model(provider, want or None, multimodal=multimodal)
    if resolved is None:
        return ()
    if pool and want == resolved and want in pool:
        return (want,)
    if pool:
        return pool
    return (resolved,)


def _log_budget(
    provider: str,
    model: str,
    requested: object,
    decision: BudgetDecision,
) -> None:
    if decision.raised_to is not None:
        log.info(
            "openvault_reasoning_budget_raised",
            provider=provider,
            model=model,
            requested=requested,
            sent=decision.raised_to,
        )
    if decision.clamped_to is not None:
        log.info(
            "openvault_output_budget_clamped",
            provider=provider,
            model=model,
            requested=requested,
            sent=decision.clamped_to,
        )


def _on_model_outcome(
    vault: KeyVault,
    fallback: FallbackManager,
    cand: ProxyCandidate,
    outcome: AttemptOutcome,
    error: str,
    model: str,
    *,
    error_text: str = "",
) -> _ModelStep:
    """Apply one model's health effects and say which way the walk goes.

    A 429 parks ``(key, model)`` only. The caller parks the whole key once
    every model on this hop has returned 429. A dead model is ejected for
    this job. 402 and auth quarantine still apply to the key and stop the hop.
    """
    if outcome.attempt_class == "success":
        _apply_candidate_outcome(vault, fallback, cand, outcome, "")
        return "served"
    if outcome.job == "dead":
        return "dead"
    # Only an HTTP 429 walks the next catalog model. Rate-limit text on any
    # other status still parks the whole key, same as before this change.
    if outcome.attempt_class == "rate_limit" and error.startswith("HTTP 429"):
        if not cand.served_local:
            fallback.record_model_park(
                cand.key_id,
                model,
                outcome.cooldown_ms,
                outcome.reason or "rate_limited",
                error_text=error_text,
            )
        return "next_model"
    if outcome.attempt_class == "model_unavailable":
        return "next_model"
    _apply_candidate_outcome(vault, fallback, cand, outcome, error, error_text=error_text)
    return "next_hop"


def _park_key_if_every_model_limited(
    fallback: FallbackManager,
    cand: ProxyCandidate,
    models: tuple[str, ...],
    *,
    limited: int,
    already_parked: int,
    cooldown_ms: int,
    reason: str,
    error_text: str = "",
) -> None:
    """Park the key only when every model on this hop came back 429."""
    if cand.served_local or not models or limited <= 0:
        return
    if limited + already_parked != len(models):
        return
    fallback.record_park(cand.key_id, cooldown_ms, reason, error_text=error_text)


def _non_retryable(
    trace: HopTrace,
    outcome: AttemptOutcome,
    errors: list[str],
    *,
    local_only: bool,
    local_fail_reason: str,
) -> tuple[int, dict[str, Any]]:
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
    error_text: str = "",
) -> None:
    """Mutate hop / provider health according to the attempt policy."""
    if outcome.attempt_class == "success":
        fallback.record_success(key_id)
        get_circuit_breaker(provider).record_success()
        return

    if outcome.candidate == "park":
        fallback.record_park(
            key_id,
            outcome.cooldown_ms,
            outcome.reason or error,
            error_text=error_text,
        )
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


PIN_UNAVAILABLE = "pin_unavailable"
STRICT_PIN_HEADER = "X-OpenVault-Strict"
_STRICT_HEADER_TRUTHY = frozenset({"1", "true", "yes"})
_PIN_PARKED = "parked"
_PIN_QUOTA = "quota_exhausted"
_PIN_CIRCUIT = "circuit_open"
_PIN_NO_HOP = "no_hop"
_PIN_NOT_IN_CATALOG = "not_in_catalog"
_PIN_PARK_REASONS = frozenset({_PIN_PARKED, _PIN_QUOTA})


def strict_header_on(value: str | None) -> bool:
    """True when ``X-OpenVault-Strict`` opts this request into a strict pin."""
    return (value or "").strip().lower() in _STRICT_HEADER_TRUTHY


def pop_strict(body: dict[str, Any]) -> bool:
    """Remove ``strict`` so it never reaches upstream. True only for JSON true."""
    if "strict" not in body:
        return False
    value = body.pop("strict")
    return value is True


def _hop_serves(provider: str, model: str, *, multimodal: bool) -> bool:
    return model in models_for(provider, multimodal=multimodal)


def _retry_seconds(cooldown_ms: int) -> int | None:
    if cooldown_ms <= 0:
        return None
    return max(1, math.ceil(cooldown_ms / 1000))


def _pin_unavailable_body(model: str, reason: str) -> dict[str, Any]:
    """Nothing served. ``model`` is the pin that failed, not a served id."""
    return {
        "error": {
            "message": "pinned model has no healthy hop",
            "type": PIN_UNAVAILABLE,
            "model": model,
            "reason": reason,
        },
        "served_provider": None,
        "served_model": None,
        "served_local": False,
    }


@dataclass
class _PinFail:
    reason: str = _PIN_NO_HOP
    retry_after_s: int | None = None


def _remember_circuit(pin_fail: _PinFail | None) -> None:
    if pin_fail is None or pin_fail.reason in _PIN_PARK_REASONS:
        return
    pin_fail.reason = _PIN_CIRCUIT


def _remember_pin_failure(pin_fail: _PinFail | None, outcome: AttemptOutcome) -> None:
    if pin_fail is None or outcome.attempt_class == "success":
        return
    if outcome.attempt_class == "quota_exhausted":
        pin_fail.reason = _PIN_QUOTA
        pin_fail.retry_after_s = _retry_seconds(outcome.cooldown_ms)
        return
    if outcome.candidate == "park":
        pin_fail.reason = _PIN_PARKED
        pin_fail.retry_after_s = _retry_seconds(outcome.cooldown_ms)


def _finish_pin(trace: HopTrace, pin: str, pin_fail: _PinFail) -> tuple[int, dict[str, Any]]:
    retry = pin_fail.retry_after_s if pin_fail.reason in _PIN_PARK_REASONS else None
    trace.error_type = PIN_UNAVAILABLE
    trace.retry_after_s = retry
    return 503, _pin_unavailable_body(pin, pin_fail.reason)


def _why_pin_blocked(
    vault: KeyVault,
    fallback: FallbackManager,
    pin: str,
    *,
    multimodal: bool,
) -> tuple[str, int | None]:
    """Why no healthy hop can serve ``pin``. Retry seconds only for a park."""
    saw_park = False
    saw_quota = False
    saw_circuit = False
    saw_hop = False
    retries: list[int] = []
    for record in vault.pooled_ordered():
        if record.custody != "pooled" or not record.enabled:
            continue
        if not _hop_serves(record.provider, pin, multimodal=multimodal):
            continue
        if record.precheck_status == "auth_fail":
            continue
        saw_hop = True
        retry = fallback.park_retry_after_s(record.id, pin)
        if (
            fallback.key_is_parked(record.id)
            and fallback.key_park_reason(record.id) == "credits_exhausted"
        ):
            saw_quota = True
            if retry is not None:
                retries.append(retry)
            continue
        if fallback.key_is_parked(record.id) or fallback.model_is_parked(record.id, pin):
            saw_park = True
            if retry is not None:
                retries.append(retry)
            continue
        if quota_blocks(record.provider, vault.db_path):
            saw_quota = True
            quota_retry = quota_retry_after_s(record.provider)
            if quota_retry is not None:
                retries.append(quota_retry)
            continue
        breaker_open = not get_circuit_breaker(record.provider).can_execute()
        if fallback.hop_circuit_is_open(record.id) or breaker_open:
            saw_circuit = True
    if not saw_hop:
        return _PIN_NO_HOP, None
    retry_after = min(retries) if retries else None
    if saw_park:
        return _PIN_PARKED, retry_after
    if saw_quota:
        return _PIN_QUOTA, retry_after
    if saw_circuit:
        return _PIN_CIRCUIT, None
    return _PIN_NO_HOP, None


def _strict_candidates(
    vault: KeyVault,
    fallback: FallbackManager,
    candidates: list[ProxyCandidate],
    pin: str,
    *,
    multimodal: bool,
) -> tuple[list[ProxyCandidate], tuple[int, dict[str, Any]] | None, int | None]:
    """Hops that serve ``pin`` exactly, or a fast ``pin_unavailable`` refusal."""
    if not catalog_contains_model(pin, multimodal=multimodal):
        return [], (503, _pin_unavailable_body(pin, _PIN_NOT_IN_CATALOG)), None
    healthy: list[ProxyCandidate] = []
    for cand in candidates:
        if not _hop_serves(cand.provider, pin, multimodal=multimodal):
            continue
        if not get_circuit_breaker(cand.provider).can_execute():
            continue
        if not cand.served_local and fallback.model_is_parked(cand.key_id, pin):
            continue
        healthy.append(cand)
    if healthy:
        return healthy, None, None
    reason, retry = _why_pin_blocked(vault, fallback, pin, multimodal=multimodal)
    return [], (503, _pin_unavailable_body(pin, reason)), retry


def _models_to_send(
    provider: str,
    requested: str | None,
    *,
    multimodal: bool,
    strict_pin: str | None,
) -> tuple[str, ...]:
    """Strict sends the pin only. Otherwise the existing per-hop catalog walk."""
    if strict_pin is not None:
        if _hop_serves(provider, strict_pin, multimodal=multimodal):
            return (strict_pin,)
        return ()
    return _models_for_hop(provider, requested, multimodal=multimodal)


@dataclass
class _Walk:
    candidates: list[ProxyCandidate]
    early: tuple[int, dict[str, Any]] | None
    local_only: bool
    strict_pin: str | None
    pin_fail: _PinFail | None


def _note_early(trace: HopTrace, early: tuple[int, dict[str, Any]]) -> None:
    err = early[1].get("error")
    if isinstance(err, dict) and err.get("type"):
        trace.error_type = str(err["type"])


def _open_walk(
    vault: KeyVault,
    fallback: FallbackManager,
    work: dict[str, Any],
    *,
    tenant: str,
    trace: HopTrace,
    path: str,
) -> _Walk:
    """Pop request flags, then either a hop list or an early refusal.

    Strict mode keeps only hops whose catalog contains the requested id.
    A parked, quota-exhausted, or open circuit on that id returns
    ``pin_unavailable`` before any upstream call.
    """
    local_only = pop_local_only(work)
    strict = pop_strict(work)
    candidates, early = _collect_candidates(
        vault, fallback, work, tenant=tenant, local_only=local_only
    )
    if early is not None:
        status, body, retry = early
        if retry is not None:
            trace.retry_after_s = retry
        noted = (status, body)
        _note_early(trace, noted)
        return _Walk(
            candidates=[],
            early=noted,
            local_only=local_only,
            strict_pin=None,
            pin_fail=None,
        )
    if vault.seal.is_sealed:
        log.warning("freeroute_refused", reason="vault_sealed", path=path)
        trace.error_type = "openvault_vault_sealed"
        sealed = _sealed_refusal()
        return _Walk(
            candidates=[], early=sealed, local_only=local_only, strict_pin=None, pin_fail=None
        )
    if not strict:
        return _Walk(
            candidates=candidates,
            early=None,
            local_only=local_only,
            strict_pin=None,
            pin_fail=None,
        )
    raw_model = work.get("model")
    pin = raw_model.strip() if isinstance(raw_model, str) else ""
    multimodal = _is_multimodal(work)
    narrowed, refusal, retry = _strict_candidates(
        vault, fallback, candidates, pin, multimodal=multimodal
    )
    if refusal is not None:
        trace.error_type = PIN_UNAVAILABLE
        trace.retry_after_s = retry
        return _Walk(
            candidates=[],
            early=refusal,
            local_only=local_only,
            strict_pin=pin,
            pin_fail=None,
        )
    return _Walk(
        candidates=narrowed,
        early=None,
        local_only=local_only,
        strict_pin=pin,
        pin_fail=_PinFail(),
    )


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
    walk = _open_walk(vault, fallback, work, tenant=tenant, trace=trace, path="chat_completions")
    if walk.early is not None:
        return walk.early
    local_only = walk.local_only
    candidates = walk.candidates
    strict_pin = walk.strict_pin
    pin_fail = walk.pin_fail

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
                _remember_circuit(pin_fail)
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
            # "auto" upstream as a model name, and every provider answered 404.
            wants_images = _is_multimodal(work)
            raw_model = work.get("model")
            requested = raw_model if isinstance(raw_model, str) else None
            models = _models_to_send(
                cand.provider,
                requested,
                multimodal=wants_images,
                strict_pin=strict_pin,
            )
            if not models:
                why = "no vision model" if wants_images else "no catalogued model"
                errors.append(f"{cand.label}: {why} for provider {cand.provider}")
                continue

            limited = 0
            already_parked = 0
            limit_cooldown = 0
            limit_reason = "rate_limited"
            limit_error = ""
            sent_any = False
            context_skips = 0
            other_skips = 0
            leave_hop = False
            for model in models:
                if not cand.served_local and fallback.model_is_parked(cand.key_id, model):
                    already_parked += 1
                    errors.append(f"{cand.label}: {model} parked")
                    continue
                decision = prepare_hop_body(
                    work,
                    provider=cand.provider,
                    model=model,
                    prompt_tokens=prompt_estimate,
                )
                if decision.body is None:
                    # Not a hop failure. This model cannot hold the prompt, so
                    # it must not count against the key's health.
                    if decision.context_exceeded:
                        context_skips += 1
                    else:
                        other_skips += 1
                    errors.append(f"{cand.label}: {decision.refusal}")
                    continue
                hop_body = decision.body
                _log_budget(cand.provider, model, work.get("max_tokens"), decision)

                trace.note_attempt()
                sent_any = True
                try:
                    resp = await client.post(url, headers=headers, json=hop_body)
                except httpx.TimeoutException:
                    outcome = classify_attempt(None, "timeout")
                    _apply_candidate_outcome(vault, fallback, cand, outcome, "timeout")
                    errors.append(f"{cand.label}: timeout")
                    if cand.served_local:
                        local_fail_reason = REASON_UNREACHABLE
                    leave_hop = True
                    break
                except (httpx.HTTPError, OSError) as exc:
                    outcome = classify_attempt(None, str(exc))
                    _apply_candidate_outcome(vault, fallback, cand, outcome, str(exc))
                    errors.append(f"{cand.label}: {exc}")
                    if cand.served_local:
                        local_fail_reason = REASON_UNREACHABLE
                    leave_hop = True
                    break

                # Upstream body is for classification only. Do not log it or
                # copy it into errors, the trace, or a usage row. A park may
                # keep a scrubbed message of at most 200 characters, not the body.
                status_code = resp.status_code
                body_text = resp.text if status_code >= 400 else ""
                outcome = classify_attempt(
                    status_code,
                    body_text if status_code >= 400 else None,
                    headers=dict(resp.headers),
                )
                err = f"HTTP {status_code}"
                snippet = provider_error_text(body_text, status=status_code)
                step = _on_model_outcome(
                    vault, fallback, cand, outcome, err, model, error_text=snippet
                )
                _remember_pin_failure(pin_fail, outcome)
                if cand.served_local and status_code >= 400:
                    local_fail_reason = _local_fail_reason(status_code, body_text, model)
                if step == "served":
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
                        payload = {"raw": resp.text}
                    return status_code, _stamp_served(payload, cand, model)
                if step == "dead":
                    errors.append(f"{cand.label}: {err} ({outcome.attempt_class})")
                    return _non_retryable(
                        trace,
                        outcome,
                        errors,
                        local_only=local_only,
                        local_fail_reason=local_fail_reason,
                    )
                errors.append(f"{cand.label}: {err} ({outcome.attempt_class})")
                if step == "next_model":
                    if outcome.attempt_class == "rate_limit":
                        limited += 1
                        limit_cooldown = outcome.cooldown_ms
                        limit_reason = outcome.reason or "rate_limited"
                        limit_error = snippet
                    continue
                leave_hop = True
                break

            if (
                not sent_any
                and context_skips
                and not other_skips
                and not already_parked
                and not limited
            ):
                context_blocked += 1
            if not leave_hop:
                _park_key_if_every_model_limited(
                    fallback,
                    cand,
                    models,
                    limited=limited,
                    already_parked=already_parked,
                    cooldown_ms=limit_cooldown,
                    reason=limit_reason,
                    error_text=limit_error,
                )

    if local_only:
        trace.error_type = "openvault_local_only_unavailable"
        return local_only_refusal(local_fail_reason)

    if context_blocked and context_blocked == considered:
        # Every candidate was refused for size and nothing was spent. Saying
        # "all hops failed" here would blame the pool for the caller's prompt.
        trace.error_type = "openvault_context_length_exceeded"
        return _context_refusal(errors)

    if strict_pin is not None and pin_fail is not None:
        return _finish_pin(trace, strict_pin, pin_fail)

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
    walk = _open_walk(vault, fallback, work, tenant=tenant, trace=trace, path="prepare_chat_stream")
    if walk.early is not None:
        return walk.early
    local_only = walk.local_only
    candidates = walk.candidates
    strict_pin = walk.strict_pin
    pin_fail = walk.pin_fail

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
                _remember_circuit(pin_fail)
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
            raw_model = payload.get("model")
            requested = raw_model if isinstance(raw_model, str) else None
            models = _models_to_send(
                cand.provider,
                requested,
                multimodal=wants_images,
                strict_pin=strict_pin,
            )
            if not models:
                why = "no vision model" if wants_images else "no catalogued model"
                errors.append(f"{cand.label}: {why} for provider {cand.provider}")
                continue

            limited = 0
            already_parked = 0
            limit_cooldown = 0
            limit_reason = "rate_limited"
            limit_error = ""
            sent_any = False
            context_skips = 0
            other_skips = 0
            leave_hop = False
            for model in models:
                if not cand.served_local and fallback.model_is_parked(cand.key_id, model):
                    already_parked += 1
                    errors.append(f"{cand.label}: {model} parked")
                    continue
                decision = prepare_hop_body(
                    payload,
                    provider=cand.provider,
                    model=model,
                    prompt_tokens=prompt_estimate,
                )
                if decision.body is None:
                    if decision.context_exceeded:
                        context_skips += 1
                    else:
                        other_skips += 1
                    errors.append(f"{cand.label}: {decision.refusal}")
                    continue
                hop_payload = decision.body
                hop_payload["stream"] = True
                _log_budget(cand.provider, model, work.get("max_tokens"), decision)

                trace.note_attempt()
                sent_any = True
                try:
                    req = client.build_request("POST", url, headers=headers, json=hop_payload)
                    resp = await client.send(req, stream=True)
                except httpx.TimeoutException:
                    outcome = classify_attempt(None, "timeout")
                    _apply_candidate_outcome(vault, fallback, cand, outcome, "timeout")
                    errors.append(f"{cand.label}: timeout")
                    if cand.served_local:
                        local_fail_reason = REASON_UNREACHABLE
                    leave_hop = True
                    break
                except (httpx.HTTPError, OSError) as exc:
                    outcome = classify_attempt(None, str(exc))
                    _apply_candidate_outcome(vault, fallback, cand, outcome, str(exc))
                    errors.append(f"{cand.label}: {exc}")
                    if cand.served_local:
                        local_fail_reason = REASON_UNREACHABLE
                    leave_hop = True
                    break

                if resp.status_code >= 400:
                    err_bytes = await resp.aread()
                    await resp.aclose()
                    # Classification only. The park row keeps a scrubbed message,
                    # never the request or the response body.
                    body_text = err_bytes.decode("utf-8", errors="replace")
                    outcome = classify_attempt(
                        resp.status_code, body_text, headers=dict(resp.headers)
                    )
                    err = f"HTTP {resp.status_code}"
                    snippet = provider_error_text(body_text, status=resp.status_code)
                    step = _on_model_outcome(
                        vault, fallback, cand, outcome, err, model, error_text=snippet
                    )
                    _remember_pin_failure(pin_fail, outcome)
                    if cand.served_local:
                        local_fail_reason = _local_fail_reason(resp.status_code, body_text, model)
                    if step == "dead":
                        errors.append(f"{cand.label}: {err} ({outcome.attempt_class})")
                        await _close_client()
                        return _non_retryable(
                            trace,
                            outcome,
                            errors,
                            local_only=local_only,
                            local_fail_reason=local_fail_reason,
                        )
                    errors.append(f"{cand.label}: {err} ({outcome.attempt_class})")
                    if step == "next_model":
                        if outcome.attempt_class == "rate_limit":
                            limited += 1
                            limit_cooldown = outcome.cooldown_ms
                            limit_reason = outcome.reason or "rate_limited"
                            limit_error = snippet
                        continue
                    leave_hop = True
                    break

                outcome = classify_attempt(resp.status_code, "", headers=dict(resp.headers))
                _on_model_outcome(vault, fallback, cand, outcome, "", model)
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

            if (
                not sent_any
                and context_skips
                and not other_skips
                and not already_parked
                and not limited
            ):
                context_blocked += 1
            if not leave_hop:
                _park_key_if_every_model_limited(
                    fallback,
                    cand,
                    models,
                    limited=limited,
                    already_parked=already_parked,
                    cooldown_ms=limit_cooldown,
                    reason=limit_reason,
                    error_text=limit_error,
                )
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

    if strict_pin is not None and pin_fail is not None:
        return _finish_pin(trace, strict_pin, pin_fail)

    trace.error_type = "openvault_fallback_exhausted"
    return 502, {
        "error": {
            "message": "all OpenVault fallback hops failed",
            "type": "openvault_fallback_exhausted",
            "details": errors,
        }
    }
