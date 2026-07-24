"""UI contract helpers for observe path responses."""

from __future__ import annotations

from typing import Literal

Severity = Literal["ok", "warn", "hot"]

HOP_LABELS: dict[str, str] = {
    "ssd_admin": "NVMe Admin",
    "driver_ioctl": "Driver IOCTL",
    "pcie_link": "PCIe Link",
    "cpu_copy": "CPU Copy",
    "host_ram": "Host DRAM",
    "ram_to_vram": "Host → VRAM",
    "gpu_compute": "GPU Compute",
}


def hop_reach_label(*, is_bottleneck: bool, severity: Severity) -> str:
    if is_bottleneck or severity == "hot":
        return "stalled here"
    if severity == "warn":
        return "slow"
    return "data reached"
