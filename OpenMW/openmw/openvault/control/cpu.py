"""CPU remediation hints (dry-run by default)."""

from __future__ import annotations

import sys
from typing import Any


def cpu_hint(*, confirm: bool, dry_run: bool) -> dict[str, Any]:
    """Suggest high-performance power plan / governor; no silent writes."""
    platform = sys.platform
    if platform == "win32":
        recommendation = "powercfg /setactive SCHEME_MIN  # High performance (manual)"
    elif platform == "linux":
        recommendation = "cpupower frequency-set -g performance  # requires privileges"
    else:
        recommendation = "Use OS energy settings for sustained CPU clocks"
    applied = False
    if confirm and not dry_run:
        # Honest: we do not auto-apply OS power plans in this scaffold.
        return {
            "ok": False,
            "capability": True,
            "dry_run": False,
            "confirm": True,
            "applied": False,
            "error": "cpu write not auto-applied; run recommendation manually",
            "recommendation": recommendation,
            "platform": platform,
        }
    return {
        "ok": True,
        "capability": True,
        "dry_run": dry_run,
        "confirm": confirm,
        "applied": applied,
        "recommendation": recommendation,
        "platform": platform,
    }
