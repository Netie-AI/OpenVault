"""In-memory route state for pass 1."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from openmw.openvault.route.types import ComboMetrics, RouteTarget, StrategyName


@dataclass
class RouteState:
    strategy: StrategyName = "priority"
    combo_name: str = "default"
    targets: list[RouteTarget] = field(default_factory=list)
    metrics: ComboMetrics = field(default_factory=ComboMetrics)

    def target_dicts(self) -> list[dict[str, object]]:
        return [
            {
                "executionKey": t.execution_key,
                "provider": t.provider,
                "modelStr": t.model_str,
                "connectionId": t.connection_id,
                "weight": t.weight,
                "priority": t.priority,
                "cost": t.cost,
            }
            for t in self.targets
        ]

    def metrics_dict(self) -> dict[str, dict[str, float | int]]:
        out: dict[str, dict[str, float | int]] = {}
        for target in self.targets:
            out[target.execution_key] = self.metrics.for_key(target.execution_key).to_dict()
        return out


_state = RouteState()
_lock = threading.Lock()


def get_route_state() -> RouteState:
    with _lock:
        return _state


def set_strategy(strategy: StrategyName) -> StrategyName:
    with _lock:
        _state.strategy = strategy
        return _state.strategy


def set_targets(targets: list[RouteTarget]) -> list[RouteTarget]:
    with _lock:
        _state.targets = list(targets)
        return _state.targets


def record_target_result(execution_key: str, *, success: bool, latency_ms: float) -> None:
    with _lock:
        _state.metrics.record(execution_key, success=success, latency_ms=latency_ms)


def reset_route_state() -> None:
    global _state
    with _lock:
        _state = RouteState()
