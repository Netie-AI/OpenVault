"""Route strategy / targets / metrics / breaker API.

Mounted by the integrator with ``app.include_router(route_router)``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from openmw.openvault.route.breaker import get_all_breaker_statuses, reset_circuit_breaker
from openmw.openvault.route.registry import get_route_state, set_strategy, set_targets
from openmw.openvault.route.strategies import apply_strategy, normalize_strategy
from openmw.openvault.route.types import RouteTarget, STRATEGY_NAMES

router = APIRouter(tags=["route"])


class StrategyBody(BaseModel):
    strategy: str


class TargetBody(BaseModel):
    execution_key: str = Field(alias="executionKey")
    provider: str
    model_str: str = Field(alias="modelStr")
    connection_id: str | None = Field(default=None, alias="connectionId")
    weight: float = 1.0
    priority: int = 100
    cost: float | None = None

    model_config = {"populate_by_name": True}


class TargetsBody(BaseModel):
    targets: list[TargetBody]


def _to_target(body: TargetBody) -> RouteTarget:
    return RouteTarget(
        execution_key=body.execution_key,
        provider=body.provider,
        model_str=body.model_str,
        connection_id=body.connection_id,
        weight=body.weight,
        priority=body.priority,
        cost=body.cost,
    )


@router.get("/api/route/strategy")
def api_get_strategy() -> dict[str, Any]:
    state = get_route_state()
    ordered = apply_strategy(state.strategy, state.targets, combo_name=state.combo_name, metrics=state.metrics)
    return {
        "strategy": state.strategy,
        "strategies": list(STRATEGY_NAMES),
        "orderedExecutionKeys": [t.execution_key for t in ordered],
    }


@router.put("/api/route/strategy")
def api_put_strategy(body: StrategyBody) -> dict[str, Any]:
    normalized = normalize_strategy(body.strategy)
    if normalized is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy {body.strategy!r}; expected one of {list(STRATEGY_NAMES)}",
        )
    set_strategy(normalized)
    state = get_route_state()
    ordered = apply_strategy(state.strategy, state.targets, combo_name=state.combo_name, metrics=state.metrics)
    return {
        "strategy": state.strategy,
        "orderedExecutionKeys": [t.execution_key for t in ordered],
    }


@router.get("/api/route/targets")
def api_get_targets() -> dict[str, Any]:
    state = get_route_state()
    ordered = apply_strategy(state.strategy, state.targets, combo_name=state.combo_name, metrics=state.metrics)
    return {
        "strategy": state.strategy,
        "targets": state.target_dicts(),
        "ordered": [
            {
                "executionKey": t.execution_key,
                "provider": t.provider,
                "modelStr": t.model_str,
            }
            for t in ordered
        ],
    }


@router.put("/api/route/targets")
def api_put_targets(body: TargetsBody) -> dict[str, Any]:
    targets = [_to_target(t) for t in body.targets]
    set_targets(targets)
    state = get_route_state()
    ordered = apply_strategy(state.strategy, state.targets, combo_name=state.combo_name, metrics=state.metrics)
    return {
        "count": len(targets),
        "orderedExecutionKeys": [t.execution_key for t in ordered],
    }


@router.get("/api/route/metrics")
def api_get_metrics() -> dict[str, Any]:
    state = get_route_state()
    return {"metrics": state.metrics_dict()}


@router.get("/api/route/breakers")
def api_get_breakers() -> dict[str, Any]:
    statuses = get_all_breaker_statuses()
    return {
        "breakers": [s.to_dict() for s in statuses],
        "count": len(statuses),
    }


@router.post("/api/route/breakers/{key}/reset")
def api_reset_breaker(key: str) -> dict[str, Any]:
    status = reset_circuit_breaker(key)
    if status is None:
        raise HTTPException(status_code=404, detail=f"No circuit breaker registered for {key!r}")
    return {"ok": True, "breaker": status.to_dict()}
