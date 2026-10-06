"""Import check: the console app and route modules load cleanly."""

from __future__ import annotations

import openmw.openvault.app as app_mod
from openmw.openvault.routers import (
    freeroute,
    health,
    key_quota,
    key_ui,
    keys,
    provider_cards,
    route,
    sentinel,
    ship,
    system,
)


def test_openvault_app_and_route_modules_import() -> None:
    assert callable(app_mod.create_app)
    assert callable(freeroute.build_freeroute_router)
    assert callable(health.build_health_router)
    assert callable(key_quota.build_key_quota_router)
    assert callable(key_ui.build_key_ui_router)
    assert keys.router is not None
    assert callable(provider_cards.build_provider_cards_router)
    assert route.router is not None
    assert sentinel.router is not None
    assert ship.router is not None
    assert callable(system.build_system_router)
