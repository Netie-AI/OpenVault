"""Combo routing strategies — OmniRoute gateway ordering, not NVIDIA llm-router.

``auto`` is OmniRoute's default: walk the vault fallback chain (priority).
It does not classify user intent with BERT/CLIP/an LLM-as-judge. That is the
deprecated NVIDIA llm-router family; FreeRoute stays a signal+breaker gateway.
``cost-optimized`` only sorts when a target has a real cost. Pricing is false
until the founder picks a model (DR-0009 / STATUS NEEDS-YOU).
"""

from __future__ import annotations

from openmw.openvault.route.rr_state import fisher_yates_shuffle, get_next_from_deck
from openmw.openvault.route.semaphore import remaining_cooldown_ms
from openmw.openvault.route.sorters import (
    order_by_p2c,
    order_lkgp,
    select_weighted_target,
    sort_by_cost,
    sort_by_headroom,
    sort_by_quota_share,
    sort_by_usage,
)
from openmw.openvault.route.types import ComboMetrics, RouteTarget, StrategyName

STRATEGY_ALIASES: dict[str, StrategyName] = {
    "auto": "auto",
    "priority": "priority",
    "weighted": "weighted",
    "fill-first": "fill-first",
    "fill_first": "fill-first",
    "round-robin": "round-robin",
    "round_robin": "round-robin",
    "p2c": "p2c",
    "random": "random",
    "least-used": "least-used",
    "least_used": "least-used",
    "cost-optimized": "cost-optimized",
    "cost_optimized": "cost-optimized",
    "reset-aware": "reset-aware",
    "reset_aware": "reset-aware",
    "reset-window": "reset-aware",
    "reset_window": "reset-aware",
    "quota-share": "quota-share",
    "quota_share": "quota-share",
    "headroom": "headroom",
    "lkgp": "lkgp",
}


def normalize_strategy(name: str) -> StrategyName | None:
    return STRATEGY_ALIASES.get(name.strip().lower())


def apply_strategy(
    strategy: StrategyName,
    targets: list[RouteTarget],
    *,
    combo_name: str = "default",
    metrics: ComboMetrics | None = None,
) -> list[RouteTarget]:
    """Reorder targets for the given strategy; unknown strategies pass through."""
    if not targets:
        return []

    if strategy in ("auto", "priority"):
        return sorted(targets, key=lambda t: (t.priority, t.execution_key))

    if strategy == "fill-first":
        return sorted(targets, key=lambda t: (t.priority, t.execution_key))

    if strategy == "weighted":
        selected = select_weighted_target(targets)
        if selected is None:
            return list(targets)
        rest = [t for t in targets if t.execution_key != selected.execution_key]
        rest.sort(key=lambda t: -t.weight)
        return [selected, *rest]

    if strategy == "round-robin":
        chosen_key = get_next_from_deck(
            f"combo:{combo_name}",
            [t.execution_key for t in targets],
        )
        chosen = next((t for t in targets if t.execution_key == chosen_key), targets[0])
        rest = [t for t in targets if t.execution_key != chosen.execution_key]
        return [chosen, *rest]

    if strategy == "p2c":
        return order_by_p2c(targets, metrics)

    if strategy == "random":
        shuffled_keys = fisher_yates_shuffle([t.execution_key for t in targets])
        by_key = {t.execution_key: t for t in targets}
        return [by_key[k] for k in shuffled_keys if k in by_key]

    if strategy == "least-used":
        return sort_by_usage(targets, metrics)

    if strategy == "cost-optimized":
        return sort_by_cost(targets)

    if strategy == "reset-aware":
        return sorted(
            targets,
            key=lambda t: (remaining_cooldown_ms(t.execution_key), t.priority, t.execution_key),
        )

    if strategy == "quota-share":
        return sort_by_quota_share(targets, metrics)

    if strategy == "headroom":
        return sort_by_headroom(targets, metrics)

    if strategy == "lkgp":
        return order_lkgp(targets, metrics)

    return list(targets)


def costs_known(targets: list[RouteTarget]) -> bool:
    """True only when at least one hop carries a numeric cost. None is not a price."""
    return any(t.cost is not None for t in targets)
