"""Severity scoring for path-trace hops (green / amber / red)."""

from __future__ import annotations

from typing import Literal

from nvme_profiler.schema import HopId, HopRecord

Severity = Literal["ok", "warn", "hot"]

# Mirror Profiler fuse theoretical BW (bytes/s) for pressure scoring.
_THEORETICAL_BW: dict[HopId, float] = {
    HopId.SSD_ADMIN: 3_500_000_000.0,
    HopId.DRIVER_IOCTL: 3_000_000_000.0,
    HopId.PCIE_LINK: 8_000_000_000.0,
    HopId.CPU_COPY: 20_000_000_000.0,
    HopId.HOST_RAM: 25_000_000_000.0,
    HopId.RAM_TO_VRAM: 12_000_000_000.0,
    HopId.GPU_COMPUTE: 50_000_000_000.0,
}


def hop_pressure(record: HopRecord) -> float:
    """Higher score = more bottleneck pressure (wait_time / theoretical_bandwidth)."""
    bw = _THEORETICAL_BW.get(record.hop_id, 1_000_000_000.0)
    wait_s = float(record.duration_ms) / 1000.0
    return float(wait_s / bw)


def hop_severity(record: HopRecord, *, bottleneck_id: str | None) -> Severity:
    """Classify hop pressure; bottleneck hop is always hot."""
    hop_id = record.hop_id.value
    if bottleneck_id is not None and hop_id == bottleneck_id:
        return "hot"
    if record.duration_ms >= 20.0 or hop_pressure(record) > 2.5e-12:
        return "warn"
    return "ok"


def severity_rank(severity: Severity) -> int:
    return {"ok": 0, "warn": 1, "hot": 2}[severity]
