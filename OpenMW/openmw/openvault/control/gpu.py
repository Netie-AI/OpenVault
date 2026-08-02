"""GPU status via NVML (read-only)."""

from __future__ import annotations

from typing import Any


def gpu_status() -> dict[str, Any]:
    """Read GPU util / temp / memory when NVML is present."""
    try:
        import pynvml
    except ImportError:
        return {"ok": False, "capability": False, "error": "pynvml not installed"}

    try:
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        devices: list[dict[str, Any]] = []
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            try:
                temp = int(pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
            except pynvml.NVMLError:
                temp = None
            devices.append(
                {
                    "index": i,
                    "name": name,
                    "util_gpu_pct": int(util.gpu),
                    "util_mem_pct": int(util.memory),
                    "mem_used_mb": int(mem.used / (1024 * 1024)),
                    "mem_total_mb": int(mem.total / (1024 * 1024)),
                    "temp_c": temp,
                }
            )
        pynvml.nvmlShutdown()
        return {"ok": True, "capability": True, "devices": devices}
    except Exception as exc:
        return {"ok": False, "capability": False, "error": str(exc)}


def gpu_power_hint(*, confirm: bool, dry_run: bool) -> dict[str, Any]:
    """GPU power/clock writes are not enabled yet."""
    return {
        "ok": False,
        "capability": False,
        "dry_run": dry_run,
        "confirm": confirm,
        "error": "gpu power/clock write unsupported — see PARKING_LOT.md",
    }
