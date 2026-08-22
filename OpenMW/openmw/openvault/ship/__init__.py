"""Tier — deploy / OpenShip / email gates."""

from __future__ import annotations

from openmw.openvault.ship.deploy import (
    build_deploy_plan,
    execute_deploy,
    list_plans,
    load_plan,
    one_press_deploy,
    run_deploy_smoke,
)
from openmw.openvault.ship.detect import DetectionInputError, detect_project
from openmw.openvault.ship.gate import GateDecision, check_gate
from openmw.openvault.ship.hosting import ready_to_ship, recommend_host
from openmw.openvault.ship.openship import (
    build_openship_plan,
    execute_openship_plan,
    list_ship_plans,
    load_ship_plan,
)

__all__ = [
    "DetectionInputError",
    "GateDecision",
    "build_deploy_plan",
    "build_openship_plan",
    "check_gate",
    "detect_project",
    "execute_deploy",
    "execute_openship_plan",
    "list_plans",
    "list_ship_plans",
    "load_plan",
    "load_ship_plan",
    "one_press_deploy",
    "ready_to_ship",
    "recommend_host",
    "run_deploy_smoke",
]
