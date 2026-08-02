"""Classify one upstream attempt into candidate + job dispositions.

Wraps ``check_fallback_error`` into the two-axis policy from
``docs/DESIGN_TIERED_QUEUE_LB.md`` §4. Pure — no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from openmw.openvault.route.breaker import TRIP_STATUS_CODES
from openmw.openvault.route.fallback_signals import check_fallback_error

AttemptClass = Literal[
    "success",
    "hard_fail",
    "rate_limit",
    "quota_exhausted",
    "auth_stale",
    "auth_fail",
    "permanent",
    "context_overflow",
    "non_retryable",
]

CandidateAction = Literal["keep", "park", "eject_for_job", "quarantine_key"]

JobAction = Literal["continue_chain", "park_job", "demote", "dead", "done"]

DEFAULT_RATE_LIMIT_PARK_MS: int = 5_000
AUTH_STALE_PARK_MS: int = 60_000
QUOTA_PARK_MS: int = 6 * 60 * 60 * 1_000


@dataclass(frozen=True)
class AttemptOutcome:
    """What to do with the key (candidate) and with the request (job)."""

    attempt_class: AttemptClass
    candidate: CandidateAction
    job: JobAction
    cooldown_ms: int
    reason: str | None
    counts_as_hard_fail: bool
    trip_provider_breaker: bool


def classify_attempt(
    status: int | None,
    error_text: str | None,
    headers: Mapping[str, str] | None = None,
) -> AttemptOutcome:
    """Map an HTTP (or transport) outcome to candidate + job actions."""
    if status is not None and 200 <= status < 300:
        return AttemptOutcome(
            attempt_class="success",
            candidate="keep",
            job="done",
            cooldown_ms=0,
            reason=None,
            counts_as_hard_fail=False,
            trip_provider_breaker=False,
        )

    # Transport failures (timeout, DNS, TLS) — no status.
    if status is None:
        return AttemptOutcome(
            attempt_class="hard_fail",
            candidate="keep",
            job="continue_chain",
            cooldown_ms=0,
            reason="transport_error",
            counts_as_hard_fail=True,
            trip_provider_breaker=True,
        )

    decision = check_fallback_error(status, error_text, headers=headers)

    if decision.permanent or decision.reason == "account_deactivated":
        return AttemptOutcome(
            attempt_class="permanent",
            candidate="quarantine_key",
            job="continue_chain",
            cooldown_ms=0,
            reason=decision.reason,
            counts_as_hard_fail=False,
            trip_provider_breaker=False,
        )

    if decision.credits_exhausted or decision.reason == "credits_exhausted":
        return AttemptOutcome(
            attempt_class="quota_exhausted",
            candidate="park",
            job="continue_chain",
            cooldown_ms=QUOTA_PARK_MS,
            reason=decision.reason,
            counts_as_hard_fail=False,
            trip_provider_breaker=False,
        )

    if decision.reason == "oauth_invalid_token":
        return AttemptOutcome(
            attempt_class="auth_stale",
            candidate="park",
            job="continue_chain",
            cooldown_ms=max(decision.cooldown_ms, AUTH_STALE_PARK_MS),
            reason=decision.reason,
            counts_as_hard_fail=False,
            trip_provider_breaker=False,
        )

    if decision.reason == "context_overflow":
        return AttemptOutcome(
            attempt_class="context_overflow",
            candidate="eject_for_job",
            job="continue_chain",
            cooldown_ms=0,
            reason=decision.reason,
            counts_as_hard_fail=False,
            trip_provider_breaker=False,
        )

    if decision.reason in ("rate_limited", "rate_limit_text") or status == 429:
        cooldown = decision.cooldown_ms if decision.cooldown_ms > 0 else DEFAULT_RATE_LIMIT_PARK_MS
        return AttemptOutcome(
            attempt_class="rate_limit",
            candidate="park",
            job="continue_chain",
            cooldown_ms=cooldown,
            reason=decision.reason or "rate_limited",
            counts_as_hard_fail=False,
            trip_provider_breaker=False,
        )

    if decision.reason == "transient" or status in TRIP_STATUS_CODES:
        return AttemptOutcome(
            attempt_class="hard_fail",
            candidate="keep",
            job="continue_chain",
            cooldown_ms=decision.cooldown_ms,
            reason=decision.reason or "transient",
            counts_as_hard_fail=True,
            trip_provider_breaker=True,
        )

    # Generic 401/403: quarantine the key, keep walking the chain.
    if status in (401, 403):
        return AttemptOutcome(
            attempt_class="auth_fail",
            candidate="quarantine_key",
            job="continue_chain",
            cooldown_ms=0,
            reason="auth_fail",
            counts_as_hard_fail=False,
            trip_provider_breaker=False,
        )

    # Bad request shape — kill the job, leave keys alone.
    if status in (400, 404, 422) or decision.reason == "non_retryable":
        return AttemptOutcome(
            attempt_class="non_retryable",
            candidate="keep",
            job="dead",
            cooldown_ms=0,
            reason=decision.reason or "non_retryable",
            counts_as_hard_fail=False,
            trip_provider_breaker=False,
        )

    return AttemptOutcome(
        attempt_class="hard_fail",
        candidate="keep",
        job="continue_chain",
        cooldown_ms=0,
        reason=decision.reason or "unknown",
        counts_as_hard_fail=True,
        trip_provider_breaker=status in TRIP_STATUS_CODES,
    )


__all__ = [
    "AUTH_STALE_PARK_MS",
    "DEFAULT_RATE_LIMIT_PARK_MS",
    "QUOTA_PARK_MS",
    "AttemptClass",
    "AttemptOutcome",
    "CandidateAction",
    "JobAction",
    "classify_attempt",
]
