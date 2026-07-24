"""Fan control — capability-gated; laptop EC usually locked."""

from __future__ import annotations

from typing import Any


def fan_set(*, percent: int, confirm: bool, dry_run: bool) -> dict[str, Any]:
    """Refuse fan writes unless a future probe proves writability."""
    pct = max(0, min(100, percent))
    return {
        "ok": False,
        "capability": False,
        "dry_run": dry_run,
        "confirm": confirm,
        "requested_percent": pct,
        "error": "fan write unsupported on this host (laptop EC locked)",
        "recommendation": "Use OEM thermal / OS cooling mode; see PARKINGLOT.md",
    }
