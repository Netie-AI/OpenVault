"""Probe which control actions this host can perform."""

from __future__ import annotations

import sys
from typing import Any


def probe_control_capabilities() -> dict[str, Any]:
    """Return honest capability map — read-first; writes often unsupported on laptops."""
    gpu_read = False
    gpu_detail = "nvidia-ml-py not available"
    try:
        import pynvml  # noqa: F401

        gpu_read = True
        gpu_detail = "NVML readable"
    except Exception as exc:  # noqa: BLE001 — capability probe must not raise
        gpu_detail = f"NVML unavailable: {exc}"

    fan_write = False
    fan_detail = "laptop EC fan write usually locked; recommend OS thermal policy"
    cpu_hint = sys.platform in {"linux", "win32"}
    return {
        "platform": sys.platform,
        "gpu_status_read": gpu_read,
        "gpu_status_detail": gpu_detail,
        "gpu_power_write": False,
        "gpu_power_detail": "force GPU clocks parked until hardware-proven",
        "cpu_hint": cpu_hint,
        "cpu_hint_detail": "governor/power-plan hints only; dry_run default",
        "fan_write": fan_write,
        "fan_detail": fan_detail,
        "actions": [
            {"id": "gpu.status", "capability": gpu_read, "mutates": False},
            {"id": "gpu.power_hint", "capability": False, "mutates": True},
            {"id": "cpu.hint", "capability": cpu_hint, "mutates": True},
            {"id": "fan.set", "capability": False, "mutates": True},
        ],
    }
