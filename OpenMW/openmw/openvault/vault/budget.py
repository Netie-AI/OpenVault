"""Output-budget policy for the gateway — the ceiling that was missing.

``providers.budget_for`` only ever *raises* a completion budget, and says so:
"Never lowers a budget - the caller may have a cost reason for a large one."
That is right for the reasoning-model floor it was written for, and it left the
gateway with no ceiling at all. Consequences we can name:

* ``max_tokens: 0`` (or negative, or NaN) is forwarded verbatim; providers 400
  on it, the hop is scored as a failure, and the next key in the pool gets the
  same invalid body. One bad field walks the whole pool.
* A prompt longer than the model's context window cannot be served by anyone,
  but we discover that one real upstream call at a time — spending a request
  against every key in the pool before answering the caller.

So this module does three things, in descending order of how certain we are:

1. **Delete an invalid ``max_tokens``.** Non-numeric, non-finite or <= 0 is not
   a budget. This is always correct and needs no table.
2. **Apply a configured ceiling.** ``OPENVAULT_MAX_OUTPUT_TOKENS`` clamps every
   request. Unset by default — a ceiling nobody asked for would refuse
   legitimate large generations (R-0005).
3. **Refuse before spending when the prompt cannot fit.** Only fires where a
   context window is actually known.

On (3): OpenVault does **not** ship invented context windows. ``ProviderSpec``
carries the field, and ``PROVIDER_CATALOG`` populates it only from a cited
source, the same discipline the model lists already follow ("Pinned from...",
"Verified against..."). Until a verified table exists, operators supply one via
``OPENVAULT_CONTEXT_WINDOWS`` and everything here stays inert rather than
guessing a number that would read exactly like a measured one.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Any

import structlog

from openmw.openvault.vault.providers import budget_for, get_provider

log = structlog.get_logger()

#: Same 4-chars-per-token heuristic the rate limiter already uses. Good enough
#: to decide "this cannot possibly fit"; never used as a billed number.
_CHARS_PER_TOKEN = 4
#: Leave room for the reply when deciding whether a prompt fits at all.
_MIN_USEFUL_OUTPUT = 16

_BUDGET_FIELDS = ("max_tokens", "max_completion_tokens", "max_output_tokens")


@dataclass(frozen=True)
class BudgetDecision:
    """What to send upstream, or why we are not going upstream at all."""

    #: Body to POST. None when this hop must be skipped.
    body: dict[str, Any] | None
    #: Populated when the hop is skipped — a reason the caller can act on.
    refusal: str = ""
    #: True when the prompt cannot fit this model's context window.
    context_exceeded: bool = False
    #: Set when we lowered a caller-supplied budget.
    clamped_to: int | None = None
    #: Set when we raised one to a reasoning floor (existing behaviour).
    raised_to: int | None = None


def configured_ceiling() -> int | None:
    """Global output ceiling, or None when the operator has not set one."""
    raw = (os.environ.get("OPENVAULT_MAX_OUTPUT_TOKENS") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        log.warning("openvault_bad_max_output_tokens", value=raw)
        return None
    return value if value > 0 else None


def _env_windows() -> dict[str, int]:
    """Operator-supplied context windows: {"groq": 131072, "groq/llama-3.1-8b-instant": 8192}."""
    raw = (os.environ.get("OPENVAULT_CONTEXT_WINDOWS") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        log.warning("openvault_bad_context_windows_json")
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in parsed.items():
        try:
            window = int(value)
        except (TypeError, ValueError):
            continue
        if window > 0:
            out[str(key).strip().lower()] = window
    return out


def context_window_for(provider: str, model: str) -> int:
    """Known context window in tokens, or 0 when we do not know it.

    Zero means "unknown", and every caller here treats unknown as "do not
    refuse" — an assumed window would reject work that would have succeeded.
    """
    windows = _env_windows()
    for key in (f"{provider}/{model}".lower(), provider.lower()):
        if key in windows:
            return windows[key]
    spec = get_provider(provider)
    return int(getattr(spec, "context_window", 0) or 0) if spec is not None else 0


def estimate_tokens_for_body(body: dict[str, Any]) -> int:
    """Rough input size: messages plus tools plus system, in tokens."""
    chars = 0
    for message in body.get("messages") or ():
        if not isinstance(message, dict):
            chars += len(str(message))
            continue
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    # A non-text part (image_url, input_audio) is not free just
                    # because it has no `text` field. Counting it as zero made
                    # the refusal gate blind to exactly the biggest inputs.
                    chars += (
                        len(text) if isinstance(text, str) else len(json.dumps(part, default=str))
                    )
                else:
                    chars += len(str(part))
        elif content is not None:
            chars += len(str(content))
        # Tool calls are part of the context the model has to read back.
        tool_calls = message.get("tool_calls")
        if tool_calls:
            chars += len(json.dumps(tool_calls, default=str))
        chars += len(str(message.get("role", "")))
    for extra in ("system", "instructions"):
        value = body.get(extra)
        if isinstance(value, str):
            chars += len(value)
    tools = body.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            chars += len(json.dumps(tool, default=str))
    return max(1, math.ceil(chars / _CHARS_PER_TOKEN))


def _valid_positive_int(value: Any) -> int | None:
    """Truncate first, then test. ``0.5`` is not a budget of zero — it is invalid.

    Testing ``value <= 0`` before ``int()`` let ``0 < v < 1`` through as ``0``,
    forwarding the exact field this module exists to delete.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        truncated = int(value)
        return truncated if truncated > 0 else None
    return None


def resolve_requested_budget(body: dict[str, Any]) -> tuple[int | None, list[str], list[str]]:
    """Read every budget field the caller sent.

    Returns ``(budget, present_fields, invalid_fields)`` where ``budget`` is the
    **minimum** of the valid ones. Taking the first match and writing it back to
    all three was wrong three ways at once: ``{max_tokens: 8000,
    max_completion_tokens: 100}`` widened a deliberate 100 to 8000;
    ``{max_tokens: 0, max_completion_tokens: 2000}`` deleted a perfectly good
    2000 along with the invalid 0; and a caller who sent only
    ``max_completion_tokens`` had a ``max_tokens`` invented for them, which some
    providers reject outright.
    """
    present: list[str] = []
    invalid: list[str] = []
    values: list[int] = []
    for field in _BUDGET_FIELDS:
        if field not in body:
            continue
        present.append(field)
        parsed = _valid_positive_int(body[field])
        if parsed is None:
            invalid.append(field)
        else:
            values.append(parsed)
    return (min(values) if values else None), present, invalid


def prepare_hop_body(
    body: dict[str, Any],
    *,
    provider: str,
    model: str,
    prompt_tokens: int | None = None,
) -> BudgetDecision:
    """Return the body to send to this hop, or a refusal.

    Order matters: drop invalid budgets, then check whether the prompt fits at
    all, then raise to a reasoning floor, then clamp to whatever ceiling is
    lower — the operator's or what is left of the context window. The floor must
    not win over the ceiling, or a reasoning model with a nearly-full context
    would get a budget that cannot fit.
    """
    hop = {**body, "model": model}
    estimate = prompt_tokens if prompt_tokens is not None else estimate_tokens_for_body(body)
    window = context_window_for(provider, model)

    if window and estimate + _MIN_USEFUL_OUTPUT >= window:
        return BudgetDecision(
            body=None,
            refusal=(f"prompt is about {estimate} tokens; {provider}/{model} tops out at {window}"),
            context_exceeded=True,
        )

    requested, present_fields, invalid_fields = resolve_requested_budget(hop)
    # Drop only what is actually invalid. A sibling field the caller got right
    # keeps working.
    for name in invalid_fields:
        hop.pop(name, None)
    write_fields = [f for f in present_fields if f not in invalid_fields]

    ceiling_candidates = [c for c in (configured_ceiling(),) if c]
    if window:
        ceiling_candidates.append(max(_MIN_USEFUL_OUTPUT, window - estimate))
    ceiling = min(ceiling_candidates) if ceiling_candidates else None

    raised_to: int | None = None
    floor = budget_for(provider, model, requested)
    if floor is not None:
        if ceiling is not None and floor > ceiling:
            # A reasoning model needs `floor` tokens before it writes any
            # content. Sending it less produces an empty completion that
            # classify_attempt scores as SUCCESS, so the walk stops on a hop
            # that answered nothing. Skip to a provider with room instead.
            return BudgetDecision(
                body=None,
                refusal=(
                    f"{provider}/{model} needs at least {floor} output tokens to answer, "
                    f"but only {ceiling} are available here"
                ),
                context_exceeded=window > 0,
            )
        requested = floor
        raised_to = floor

    clamped_to: int | None = None
    if requested is not None and ceiling is not None and requested > ceiling:
        requested = ceiling
        clamped_to = ceiling
        raised_to = None
    elif requested is None and ceiling is not None and configured_ceiling():
        # An operator ceiling applies even when the caller named no budget --
        # that is the whole point of a denial-of-wallet guard.
        requested = ceiling
        clamped_to = ceiling
        write_fields = ["max_tokens"]

    if requested is not None:
        # Only fields the caller actually sent (plus max_tokens when an operator
        # ceiling introduced the budget). Inventing max_tokens for a caller who
        # sent max_completion_tokens earns a 400 from providers that reject it.
        for name in write_fields or ["max_tokens"]:
            hop[name] = requested

    return BudgetDecision(
        body=hop,
        clamped_to=clamped_to,
        raised_to=raised_to,
    )


__all__ = [
    "BudgetDecision",
    "configured_ceiling",
    "context_window_for",
    "estimate_tokens_for_body",
    "prepare_hop_body",
]
