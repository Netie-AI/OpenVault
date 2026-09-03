"""Account fallback signal tables and Retry-After parsing ladder."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

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

_custom_banned_signals: list[str] = []


def set_custom_banned_signals(signals: list[str]) -> None:
    _custom_banned_signals.clear()
    _custom_banned_signals.extend(signals)


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


def parse_delay_string(value: str | int | float | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    ms_match = re.fullmatch(r"(\d+)\s*ms", text, re.I)
    if ms_match:
        return int(ms_match.group(1))
    sec_match = re.fullmatch(r"(\d+)\s*s", text, re.I)
    if sec_match:
        return int(sec_match.group(1)) * 1000
    min_match = re.fullmatch(r"(\d+)\s*m", text, re.I)
    if min_match:
        return int(min_match.group(1)) * 60_000
    hr_match = re.fullmatch(r"(\d+)\s*h", text, re.I)
    if hr_match:
        return int(hr_match.group(1)) * 3_600_000
    try:
        return int(text) * 1000
    except ValueError:
        return None


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
