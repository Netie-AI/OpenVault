"""Target ordering helpers — weighted roulette, P2C, usage, cost."""

from __future__ import annotations

import math
import random
from typing import TypeVar

from openmw.openvault.route.breaker import STATE_HALF_OPEN, STATE_OPEN, get_circuit_breaker
from openmw.openvault.route.types import ComboMetrics, RouteTarget

T = TypeVar("T", bound=RouteTarget)


def select_weighted_target(targets: list[T]) -> T | None:
    """Cumulative-weight roulette; uniform pick when total weight is zero."""
    if not targets:
        return None
    total = sum(max(0.0, t.weight) for t in targets)
    if total <= 0:
        return random.choice(targets)
    roll = random.random() * total
    for target in targets:
        roll -= max(0.0, target.weight)
        if roll <= 0:
            return target
    return targets[-1]


def _p2c_score(target: RouteTarget, metrics: ComboMetrics | None) -> float:
    breaker = get_circuit_breaker(target.provider)
    breaker_state = breaker.get_status().state
    if breaker_state == STATE_OPEN:
        return float("-inf")
    m = metrics.for_key(target.execution_key) if metrics else None
    success_rate = m.success_rate if m else 50.0
    success_score = success_rate / 100.0
    avg_latency = m.avg_latency_ms if m else 0.0
    latency_score = 1.0 / math.log10(avg_latency + 10) if avg_latency > 0 else 0.25
    penalty = 0.25 if breaker_state == STATE_HALF_OPEN else 0.0
    return success_score + latency_score - penalty


def order_by_p2c(targets: list[T], metrics: ComboMetrics | None) -> list[T]:
    """Pick two random targets; higher P2C score wins the front slot."""
    if len(targets) <= 1:
        return list(targets)
    first_idx = random.randrange(len(targets))
    second_idx = random.randrange(len(targets) - 1)
    if second_idx >= first_idx:
        second_idx += 1
    first = targets[first_idx]
    second = targets[second_idx]
    winner_idx = second_idx if _p2c_score(second, metrics) > _p2c_score(first, metrics) else first_idx
    winner = targets[winner_idx]
    rest = [t for i, t in enumerate(targets) if i != winner_idx]
    return [winner, *rest]


def sort_by_usage(targets: list[T], metrics: ComboMetrics | None) -> list[T]:
    """Least-used first, keyed on execution_key so sibling accounts stay distinct."""

    def usage_key(t: T) -> int:
        if metrics is None:
            return 0
        return metrics.for_key(t.execution_key).requests

    return sorted(targets, key=usage_key)


def sort_by_cost(targets: list[T]) -> list[T]:
    """Cheapest first; unknown cost sorts last."""

    def cost_key(t: T) -> float:
        if t.cost is None:
            return float("inf")
        return t.cost

    return sorted(targets, key=cost_key)
