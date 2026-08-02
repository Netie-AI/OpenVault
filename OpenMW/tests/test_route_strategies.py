"""Routing strategy ordering tests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.route.registry import reset_route_state, set_targets
from openmw.openvault.route.rr_state import reset_decks
from openmw.openvault.route.strategies import apply_strategy
from openmw.openvault.route.types import ComboMetrics, RouteTarget
from openmw.openvault.routers.route import router as route_router


def _targets() -> list[RouteTarget]:
    return [
        RouteTarget("a", "openai", "openai/gpt-4", weight=1.0, priority=10, cost=3.0),
        RouteTarget("b", "anthropic", "anthropic/claude", weight=5.0, priority=20, cost=1.0),
        RouteTarget("c", "google", "google/gemini", weight=2.0, priority=30, cost=2.0),
    ]


def test_priority_orders_by_priority() -> None:
    ordered = apply_strategy("priority", _targets())
    assert [t.execution_key for t in ordered] == ["a", "b", "c"]


def test_cost_optimized_cheapest_first() -> None:
    ordered = apply_strategy("cost-optimized", _targets())
    assert ordered[0].execution_key == "b"


def test_least_used_prefers_low_request_count() -> None:
    metrics = ComboMetrics()
    metrics.record("a", success=True, latency_ms=100)
    metrics.record("a", success=True, latency_ms=100)
    metrics.record("b", success=True, latency_ms=50)
    ordered = apply_strategy("least-used", _targets(), metrics=metrics)
    assert ordered[0].execution_key == "c"


def test_round_robin_deck_cycles_without_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_decks()
    targets = _targets()
    keys: list[str] = []
    for _ in range(3):
        ordered = apply_strategy("round-robin", targets, combo_name="test-rr")
        keys.append(ordered[0].execution_key)
    assert len(set(keys)) == 3


def test_weighted_puts_selected_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openmw.openvault.route.sorters.random.random",
        lambda: 0.5,
    )
    ordered = apply_strategy("weighted", _targets())
    assert ordered[0].execution_key == "b"


def test_route_api_strategy_roundtrip() -> None:
    reset_route_state()
    app = FastAPI()
    app.include_router(route_router)
    client = TestClient(app)

    put = client.put("/api/route/strategy", json={"strategy": "cost-optimized"})
    assert put.status_code == 200
    assert put.json()["strategy"] == "cost-optimized"

    set_targets(_targets())
    body = client.get("/api/route/targets").json()
    assert body["ordered"][0]["executionKey"] == "b"


def test_route_api_breakers_empty() -> None:
    reset_route_state()
    app = FastAPI()
    app.include_router(route_router)
    client = TestClient(app)
    resp = client.get("/api/route/breakers")
    assert resp.status_code == 200
    assert "breakers" in resp.json()
