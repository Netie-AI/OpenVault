"""Shared types for OmniRoute routing internals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

StrategyName = Literal[
    "priority",
    "weighted",
    "fill-first",
    "round-robin",
    "p2c",
    "random",
    "least-used",
    "cost-optimized",
]

STRATEGY_NAMES: tuple[StrategyName, ...] = (
    "priority",
    "weighted",
    "fill-first",
    "round-robin",
    "p2c",
    "random",
    "least-used",
    "cost-optimized",
)

BreakerProfileName = Literal["oauth", "api_key", "local"]


@dataclass
class RouteTarget:
    """A routable upstream target (connection + model)."""

    execution_key: str
    provider: str
    model_str: str
    connection_id: str | None = None
    weight: float = 1.0
    priority: int = 100
    cost: float | None = None


@dataclass
class TargetMetrics:
    """Per-target success/latency counters for P2C and least-used."""

    requests: int = 0
    successes: int = 0
    total_latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.requests <= 0:
            return 50.0
        return (self.successes / self.requests) * 100.0

    @property
    def avg_latency_ms(self) -> float:
        if self.requests <= 0:
            return 0.0
        return self.total_latency_ms / self.requests

    def to_dict(self) -> dict[str, float | int]:
        return {
            "requests": self.requests,
            "successes": self.successes,
            "successRate": round(self.success_rate, 2),
            "avgLatencyMs": round(self.avg_latency_ms, 2),
            "uses": self.requests,
        }


@dataclass
class ComboMetrics:
    """Metrics keyed by execution_key (not model_str) so sibling accounts stay distinct."""

    by_execution_key: dict[str, TargetMetrics] = field(default_factory=dict)

    def for_key(self, execution_key: str) -> TargetMetrics:
        if execution_key not in self.by_execution_key:
            self.by_execution_key[execution_key] = TargetMetrics()
        return self.by_execution_key[execution_key]

    def record(self, execution_key: str, *, success: bool, latency_ms: float) -> None:
        m = self.for_key(execution_key)
        m.requests += 1
        if success:
            m.successes += 1
        m.total_latency_ms += max(0.0, latency_ms)
