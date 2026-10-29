"""Account fallback signal tables and Retry-After parsing ladder."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

from openmw.openvault.vault.providers import get_provider
from openmw.openvault.vault.quota import seconds_until_reset

# T06: permanent account deactivation signals
ACCOUNT_DEACTIVATED_SIGNALS: tuple[str, ...] = (
    "account_deactivated",
    "account has been deactivated",
    "account has been disabled",
    "your account has been suspended",
    "this account is deactivated",
    "verify your account to continue",
    "this service has been disabled in this account for violation",
    "this service has been disabled in this account",
)

# T10: billing credits exhausted
CREDITS_EXHAUSTED_SIGNALS: tuple[str, ...] = (
    "insufficient_quota",
    "billing_hard_limit_reached",
    "exceeded your current quota",
    "exceeded your current usage quota",
    "credit_balance_too_low",
    "your credit balance is too low",
    "credits exhausted",
    "out of credits",
    "payment required",
    "free tier of the model has been exhausted",
    "insufficient balance",
    "insufficient_balance",
    "insufficient account balance",
)

# T11: OAuth token invalid/expired (not permanent deactivation)
OAUTH_INVALID_TOKEN_SIGNALS: tuple[str, ...] = (
    "invalid authentication credentials",
    "oauth 2",
    "login cookie",
    "valid authentication credential",
    "invalid credentials",
)

CONTEXT_OVERFLOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\binput is too long\b", re.I),
    re.compile(r"\binput too long\b", re.I),
    re.compile(r"\bcontext.*(too long|exceeded|overflow|limit)", re.I),
    re.compile(r"\btoo many tokens\b", re.I),
    re.compile(r"\bprompt is too long\b", re.I),
    re.compile(r"\bcontext window", re.I),
    re.compile(r"\bmaximum context", re.I),
    re.compile(r"\bmax.*token", re.I),
    re.compile(r"\btoken limit", re.I),
    re.compile(r"\brequest too large\b", re.I),
)

RATE_LIMIT_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"high.?frequency", re.I),
    re.compile(r"non-compliant", re.I),
    re.compile(r"too many requests", re.I),
    re.compile(r"rate.?limit", re.I),
    re.compile(r"频繁"),
    re.compile(r"频率"),
)

# Gemini RetryInfo.retryDelay is a protobuf JSON duration, for example "27s".
_RETRY_DELAY_S = re.compile(r"^(\d+(?:\.\d+)?)s$")
# Per-minute when RetryInfo is absent or rejected.
_GOOGLE_SHORT_429_MS = 60_000
# A parsed delay never parks longer than an hour, and never past the daily reset.
_RETRY_CAP_S = 3600.0

_custom_banned_signals: list[str] = []


def get_merged_banned_signals() -> list[str]:
    if not _custom_banned_signals:
        return list(ACCOUNT_DEACTIVATED_SIGNALS)
    return list(ACCOUNT_DEACTIVATED_SIGNALS) + list(_custom_banned_signals)


def is_account_deactivated(error_text: str) -> bool:
    lower = str(error_text or "").lower()
    return any(sig in lower for sig in get_merged_banned_signals())


def is_credits_exhausted(error_text: str) -> bool:
    lower = str(error_text or "").lower()
    return any(sig in lower for sig in CREDITS_EXHAUSTED_SIGNALS)


def is_oauth_invalid_token(error_text: str) -> bool:
    lower = str(error_text or "").lower()
    return any(sig in lower for sig in OAUTH_INVALID_TOKEN_SIGNALS)


def is_context_overflow(error_text: str) -> bool:
    text = str(error_text or "")
    return any(p.search(text) for p in CONTEXT_OVERFLOW_PATTERNS)


def is_rate_limit_text(error_text: str) -> bool:
    text = str(error_text or "")
    return any(p.search(text) for p in RATE_LIMIT_TEXT_PATTERNS)


def _compute_duration_ms(match: re.Match[str]) -> int:
    hours = int(match.group(1)[:-1]) if match.group(1) else 0
    minutes = int(match.group(2)[:-1]) if match.group(2) else 0
    seconds = int(match.group(3)[:-1]) if match.group(3) else 0
    return ((hours * 3600) + (minutes * 60) + seconds) * 1000


def parse_retry_from_error_text(error_text: str | None) -> int | None:
    """Parse free-text retry hints (reset after XhYmZs, resets in …)."""
    if not error_text:
        return None
    msg = str(error_text)

    retry_match = re.search(r"retry\s+after\s+(\d+)\s*s", msg, re.I)
    if retry_match:
        return int(retry_match.group(1)) * 1000

    for pattern in (
        r"reset after (\d+h)?(\d+m)?(\d+s)?",
        r"will reset after (\d+h)?(\d+m)?(\d+s)?",
        r"resets? in (\d+h)?(\d+m)?(\d+s)?",
    ):
        match = re.search(pattern, msg, re.I)
        if match and (match.group(1) or match.group(2) or match.group(3)):
            return _compute_duration_ms(match)

    return None


def parse_reset_from_headers(headers: Mapping[str, str] | None) -> int | None:
    """Return absolute reset timestamp (ms) from Retry-After / X-RateLimit-Reset."""
    if not headers:
        return None
    lowered = {k.lower(): v for k, v in headers.items()}

    retry_after = lowered.get("retry-after")
    if retry_after:
        stripped = retry_after.strip()
        if stripped.isdigit():
            return int(time.time() * 1000) + int(stripped) * 1000
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(retry_after)
            return int(dt.timestamp() * 1000)
        except (TypeError, ValueError, OverflowError):
            pass

    rl_reset = lowered.get("x-ratelimit-reset")
    if rl_reset:
        try:
            ts = int(rl_reset)
            return ts if ts > 10_000_000_000 else ts * 1000
        except ValueError:
            pass

    return None


def _reset_cap_s() -> float:
    """Seconds until Google's daily reset. ``3600`` when the catalog has no zone."""
    spec = get_provider("google")
    tz = spec.quota_reset_tz if spec is not None and spec.quota_reset_tz else ""
    if not tz:
        return _RETRY_CAP_S
    return seconds_until_reset(tz)


def _capped_delay_ms(seconds: float) -> int | None:
    """Cap a finite positive delay at ``min(3600, seconds until daily reset)``.

    A non-finite value raises ``OverflowError`` so the caller can drop the
    structured parse and keep the old text classification.
    """
    if not math.isfinite(seconds):
        raise OverflowError("non-finite retryDelay")
    if seconds <= 0:
        return None
    capped = min(float(seconds), _RETRY_CAP_S, _reset_cap_s())
    if capped <= 0:
        return None
    return max(1, int(capped * 1000.0))


def _retry_delay_ms(value: object) -> int | None:
    """Milliseconds from a Gemini ``RetryInfo.retryDelay`` string.

    Negative, non-numeric, and non-finite values are rejected. A finite delay
    is capped at ``min(3600 s, time until the provider's daily reset)``.
    """
    if not isinstance(value, str):
        return None
    match = _RETRY_DELAY_S.fullmatch(value.strip())
    if match is None:
        return None
    seconds = float(match.group(1))
    return _capped_delay_ms(seconds)


def _quota_kinds(violations: object) -> tuple[bool, bool]:
    """``(per_day, per_minute)`` from ``QuotaFailure.violations[].quotaId``."""
    per_day = False
    per_minute = False
    if not isinstance(violations, list):
        return per_day, per_minute
    for item in violations:
        if not isinstance(item, dict):
            continue
        quota_id = item.get("quotaId")
        if not isinstance(quota_id, str):
            continue
        lowered = quota_id.lower()
        if "perday" in lowered:
            per_day = True
        if "perminute" in lowered:
            per_minute = True
    return per_day, per_minute


def _google_429_decision(error_text: str) -> FallbackDecision | None:
    """Short park for an explicit per-minute ``quotaId``, else None.

    None means the old text tables still decide. That covers a body with no
    structured details, a per-day quota id, and any quota id that is not
    per-minute. A parse failure is None as well, so the handler does not raise.
    """
    text = error_text.strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return None
        err = payload.get("error")
        source = err if isinstance(err, dict) else payload
        return _decision_from_google_details(source.get("details"))
    except (ValueError, OverflowError, RecursionError):
        return None


def _decision_from_google_details(details: object) -> FallbackDecision | None:
    if not isinstance(details, list):
        return None
    per_day = False
    per_minute = False
    retry_ms: int | None = None
    for item in details:
        if not isinstance(item, dict):
            continue
        kind = item.get("@type")
        if not isinstance(kind, str):
            continue
        lowered = kind.lower()
        if lowered.endswith("google.rpc.quotafailure"):
            day, minute = _quota_kinds(item.get("violations"))
            per_day = per_day or day
            per_minute = per_minute or minute
        elif lowered.endswith("google.rpc.retryinfo"):
            parsed = _retry_delay_ms(item.get("retryDelay"))
            if parsed is not None:
                retry_ms = parsed
    # Only an explicit per-minute id is a short park. Per-day wins if both match.
    if not per_minute or per_day:
        return None
    if retry_ms is None:
        retry_ms = _capped_delay_ms(60.0)
    if retry_ms is None:
        retry_ms = _GOOGLE_SHORT_429_MS
    return FallbackDecision(
        True,
        retry_ms,
        reason="rate_limited",
        used_upstream_retry_hint=retry_ms != _GOOGLE_SHORT_429_MS,
        short_rate_limit=True,
    )


def parse_upstream_retry_hint_ms(
    headers: Mapping[str, str] | None,
    error_text: str | None,
) -> int | None:
    """Ladder: headers first, then free-text body hints."""
    reset_ms = parse_reset_from_headers(headers)
    if reset_ms is not None:
        wait = max(reset_ms - int(time.time() * 1000), 0)
        if wait > 0:
            return wait
    body_hint = parse_retry_from_error_text(error_text)
    if body_hint and body_hint > 0:
        return body_hint
    return None


@dataclass(frozen=True)
class FallbackDecision:
    should_fallback: bool
    cooldown_ms: int
    reason: str | None = None
    permanent: bool = False
    credits_exhausted: bool = False
    used_upstream_retry_hint: bool = False
    short_rate_limit: bool = False


def check_fallback_error(
    status: int,
    error_text: str | None,
    *,
    headers: Mapping[str, str] | None = None,
    base_cooldown_ms: int = 5_000,
) -> FallbackDecision:
    """Lightweight fallback classifier — signal tables + retry ladder only."""
    text = str(error_text or "")

    if is_account_deactivated(text):
        return FallbackDecision(False, 0, reason="account_deactivated", permanent=True)

    # Before the credits phrase table: Gemini reuses that phrase for RPM.
    if status == 429:
        structured = _google_429_decision(text)
        if structured is not None:
            return structured

    if is_credits_exhausted(text) or status == 402:
        return FallbackDecision(True, 0, reason="credits_exhausted", credits_exhausted=True)

    if is_oauth_invalid_token(text) and status in (401, 403):
        return FallbackDecision(True, base_cooldown_ms, reason="oauth_invalid_token")

    if is_context_overflow(text) and status == 400:
        return FallbackDecision(True, 0, reason="context_overflow")

    if is_rate_limit_text(text):
        hint = parse_upstream_retry_hint_ms(headers, text)
        if hint:
            return FallbackDecision(
                True,
                hint,
                reason="rate_limit_text",
                used_upstream_retry_hint=True,
            )
        return FallbackDecision(True, base_cooldown_ms, reason="rate_limit_text")

    if status == 429:
        hint = parse_upstream_retry_hint_ms(headers, text)
        if hint:
            return FallbackDecision(
                True,
                hint,
                reason="rate_limited",
                used_upstream_retry_hint=True,
            )
        return FallbackDecision(True, base_cooldown_ms, reason="rate_limited")

    retryable = status in (408, 500, 502, 503, 504)
    if retryable:
        hint = parse_upstream_retry_hint_ms(headers, text)
        if hint:
            return FallbackDecision(
                True,
                hint,
                reason="transient",
                used_upstream_retry_hint=True,
            )
        return FallbackDecision(True, base_cooldown_ms, reason="transient")

    return FallbackDecision(False, 0, reason="non_retryable")
