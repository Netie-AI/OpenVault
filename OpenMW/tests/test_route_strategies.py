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


def test_auto_matches_priority_order() -> None:
    """OmniRoute auto is the fallback chain, not an intent classifier."""
    auto = apply_strategy("auto", _targets())
    priority = apply_strategy("priority", _targets())
    assert [t.execution_key for t in auto] == [t.execution_key for t in priority]


def test_reset_aware_sends_cooling_hops_last() -> None:
    """OmniRoute reset-aware: Retry-After park last, not an intent classifier."""
    from openmw.openvault.route.semaphore import mark_rate_limited, remaining_cooldown_ms, reset_all

    reset_all()
    mark_rate_limited("a", 60_000)
    ordered = apply_strategy("reset-aware", _targets())
    assert ordered[-1].execution_key == "a"
    assert remaining_cooldown_ms("a") > 0
    reset_all()


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


def test_quota_share_sends_overused_same_provider_key_last() -> None:
    """OmniRoute equal-share hop order; not the 3-policy allow/block dashboard."""
    keys = [
        RouteTarget("oa-hot", "openai", "openai/gpt-4", priority=10),
        RouteTarget("oa-cool", "openai", "openai/gpt-4", priority=20),
        RouteTarget("oa-ok", "openai", "openai/gpt-4", priority=30),
        RouteTarget("an-other", "anthropic", "anthropic/claude", priority=5),
    ]
    metrics = ComboMetrics()
    for _ in range(10):
        metrics.record("oa-hot", success=True, latency_ms=20)
    metrics.record("oa-cool", success=True, latency_ms=20)
    metrics.record("oa-ok", success=True, latency_ms=20)
    ordered = apply_strategy("quota-share", keys, metrics=metrics)
    openai_order = [t.execution_key for t in ordered if t.provider == "openai"]
    assert openai_order[-1] == "oa-hot"
    assert openai_order[0] in {"oa-cool", "oa-ok"}
    keys_order = [t.execution_key for t in ordered]
    assert keys_order.index("an-other") < keys_order.index("oa-hot")


def test_headroom_parks_cooling_hops_and_prefers_unused() -> None:
    from openmw.openvault.route.semaphore import mark_rate_limited, reset_all

    reset_all()
    mark_rate_limited("c", 60_000)
    metrics = ComboMetrics()
    metrics.record("a", success=True, latency_ms=10)
    ordered = apply_strategy("headroom", _targets(), metrics=metrics)
    assert ordered[-1].execution_key == "c"
    assert ordered[0].execution_key == "b"
    reset_all()


def test_lkgp_pins_last_successful_hop_first() -> None:
    metrics = ComboMetrics()
    metrics.record("c", success=True, latency_ms=40)
    ordered = apply_strategy("lkgp", _targets(), metrics=metrics)
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
    client = TestClient(app, client=("127.0.0.1", 5555))

    empty = client.get("/api/route/strategy")
    assert empty.status_code == 200
    assert empty.json()["priced"] is False
    assert empty.json()["strategy"] == "auto"
    assert empty.json()["costOptimizedAvailable"] is False
    assert "cost-optimized" in empty.json()["unavailableStrategies"]
    assert "auto" in empty.json()["strategies"]
    assert "reset-aware" in empty.json()["strategies"]
    assert "quota-share" in empty.json()["strategies"]
    assert "headroom" in empty.json()["strategies"]
    assert "lkgp" in empty.json()["strategies"]
    assert empty.json()["hops"] == []
    assert empty.json()["priced"] is False

    refused = client.put("/api/route/strategy", json={"strategy": "cost-optimized"})
    assert refused.status_code == 409
    assert "priced=false" in refused.json()["detail"]

    set_targets(_targets())
    put = client.put("/api/route/strategy", json={"strategy": "cost-optimized"})
    assert put.status_code == 200
    assert put.json()["strategy"] == "cost-optimized"
    assert put.json()["priced"] is False
    assert put.json()["costOptimizedAvailable"] is True

    body = client.get("/api/route/targets").json()
    assert body["ordered"][0]["executionKey"] == "b"


def test_route_api_breakers_empty() -> None:
    reset_route_state()
    app = FastAPI()
    app.include_router(route_router)
    client = TestClient(app, client=("127.0.0.1", 5555))
    resp = client.get("/api/route/breakers")
    assert resp.status_code == 200
    assert "breakers" in resp.json()


def test_combo_reorder_reset_aware_parks_last() -> None:
    """Gateway hop walk (not just the strategy API) respects Retry-After parks."""
    from openmw.openvault.route.registry import reset_route_state, set_strategy
    from openmw.openvault.route.semaphore import mark_rate_limited, reset_all
    from openmw.openvault.vault.proxy import _combo_reorder
    from openmw.openvault.vault.store import KeyRecord

    def rec(kid: str) -> KeyRecord:
        return KeyRecord(
            id=kid,
            label=kid,
            provider="openai",
            role="primary",
            base_url="https://api.openai.com/v1",
            masked_secret="sk-****",
            enabled=True,
            priority=10,
            precheck_status="ok",
            last_latency_ms=None,
            last_error=None,
            last_precheck_at=None,
            created_at=0.0,
            updated_at=0.0,
        )

    reset_all()
    reset_route_state()
    set_strategy("reset-aware")
    mark_rate_limited("k1", 30_000)
    ordered = _combo_reorder([rec("k1"), rec("k2")])
    assert [r.id for r in ordered] == ["k2", "k1"]
    reset_all()
    reset_route_state()


def test_combo_reorder_auto_parks_cooling_last() -> None:
    """OmniRoute auto still walks the chain, but does not hammer a 429 hop first."""
    from openmw.openvault.route.registry import reset_route_state, set_strategy
    from openmw.openvault.route.semaphore import mark_rate_limited, reset_all
    from openmw.openvault.vault.proxy import _combo_reorder
    from openmw.openvault.vault.store import KeyRecord

    def rec(kid: str) -> KeyRecord:
        return KeyRecord(
            id=kid,
            label=kid,
            provider="openai",
            role="primary",
            base_url="https://api.openai.com/v1",
            masked_secret="sk-****",
            enabled=True,
            priority=10,
            precheck_status="ok",
            last_latency_ms=None,
            last_error=None,
            last_precheck_at=None,
            created_at=0.0,
            updated_at=0.0,
        )

    reset_all()
    reset_route_state()
    set_strategy("auto")
    mark_rate_limited("k1", 30_000)
    ordered = _combo_reorder([rec("k1"), rec("k2")])
    assert [r.id for r in ordered] == ["k2", "k1"]
    reset_all()
    reset_route_state()


def test_wants_free_help_is_keyword_only() -> None:
    from openmw.openvault.route.task_help import wants_free_help

    assert wants_free_help(
        {"messages": [{"role": "user", "content": "Get a free key and register"}]}
    )
    assert not wants_free_help(
        {"messages": [{"role": "user", "content": "Summarize this invoice"}]}
    )


def test_combo_reorder_auto_help_walks_free_before_primary() -> None:
    from openmw.openvault.route.registry import reset_route_state, set_strategy
    from openmw.openvault.route.semaphore import reset_all
    from openmw.openvault.vault.proxy import _combo_reorder
    from openmw.openvault.vault.store import KeyRecord

    def rec(kid: str, role: str, priority: int) -> KeyRecord:
        return KeyRecord(
            id=kid,
            label=kid,
            provider="openai",
            role=role,  # type: ignore[arg-type]
            base_url="https://api.openai.com/v1",
            masked_secret="sk-****",
            enabled=True,
            priority=priority,
            precheck_status="ok",
            last_latency_ms=None,
            last_error=None,
            last_precheck_at=None,
            created_at=0.0,
            updated_at=0.0,
        )

    reset_all()
    reset_route_state()
    set_strategy("auto")
    hops = [rec("paid", "primary", 1), rec("gratis", "free", 50)]
    plain = _combo_reorder(hops, {"messages": [{"role": "user", "content": "hello"}]})
    assert [r.id for r in plain] == ["paid", "gratis"]
    helped = _combo_reorder(
        hops,
        {"messages": [{"role": "user", "content": "help me route a free model"}]},
    )
    assert [r.id for r in helped] == ["gratis", "paid"]
    reset_route_state()
