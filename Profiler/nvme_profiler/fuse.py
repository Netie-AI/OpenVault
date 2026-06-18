"""Fuse SSD-side timing with external profiler captures."""

from __future__ import annotations

from nvme_profiler.schema import HopId, HopRecord, PathTraceReport

# Theoretical bandwidth ceilings (bytes/s) for bottleneck scoring — conservative laptop defaults.
_THEORETICAL_BW: dict[HopId, float] = {
    HopId.SSD_ADMIN: 3_500_000_000.0,  # ~3.5 GB/s NVMe seq
    HopId.DRIVER_IOCTL: 3_000_000_000.0,
    HopId.PCIE_LINK: 8_000_000_000.0,  # PCIe 4.0 x4
    HopId.CPU_COPY: 20_000_000_000.0,
    HopId.HOST_RAM: 25_000_000_000.0,
    HopId.RAM_TO_VRAM: 12_000_000_000.0,  # PCIe-limited H2D
    HopId.GPU_COMPUTE: 50_000_000_000.0,
}


def _hop_pressure(record: HopRecord) -> float:
    """Higher score = more bottleneck pressure (wait_time / theoretical_bandwidth)."""
    bw = _THEORETICAL_BW.get(record.hop_id, 1_000_000_000.0)
    wait_s = record.duration_ms / 1000.0
    return wait_s / bw


def compute_bottleneck_hop(timeline: list[HopRecord]) -> HopId | None:
    """Return hop with highest (wait_time / theoretical_bandwidth) pressure."""
    if not timeline:
        return None
    return max(timeline, key=_hop_pressure).hop_id


def compute_gpu_idle_pct_waiting_on_io(timeline: list[HopRecord]) -> float | None:
    """Estimate GPU idle % attributable to I/O waits (ssd + driver + pcie + ram_to_vram)."""
    io_hops = {
        HopId.SSD_ADMIN,
        HopId.DRIVER_IOCTL,
        HopId.PCIE_LINK,
        HopId.RAM_TO_VRAM,
    }
    io_ms = sum(r.duration_ms for r in timeline if r.hop_id in io_hops)
    gpu_ms = sum(r.duration_ms for r in timeline if r.hop_id == HopId.GPU_COMPUTE)
    total = io_ms + gpu_ms
    if total <= 0:
        return None
    return round(100.0 * io_ms / total, 2)


def fuse_admin_timings(
    admin_records: list[dict[str, object]],
    nsys_hops: list[HopRecord] | None = None,
) -> list[HopRecord]:
    """Merge nvme-sentinel admin_command_timing records with optional nsys hops."""
    timeline: list[HopRecord] = []
    t0 = 0.0
    for idx, rec in enumerate(admin_records):
        raw_ms = rec.get("duration_ms", 0)
        duration_ms = float(raw_ms) if isinstance(raw_ms, (int, float)) else 0.0
        raw_len = rec.get("data_len", 0)
        data_len = int(raw_len) if isinstance(raw_len, (int, float)) else 0
        adapter = rec.get("adapter", "")
        hop = HopRecord(
            hop_id=HopId.SSD_ADMIN if idx == 0 else HopId.DRIVER_IOCTL,
            start_ts=t0,
            end_ts=t0 + duration_ms / 1000.0,
            bytes_moved=data_len,
            duration_ms=duration_ms,
            notes=str(adapter),
        )
        timeline.append(hop)
        t0 = hop.end_ts
    if nsys_hops:
        timeline.extend(nsys_hops)
    return timeline


def enrich_path_trace(report: PathTraceReport) -> PathTraceReport:
    """Compute bottleneck_hop and gpu_idle_pct from hop_timeline."""
    bottleneck = compute_bottleneck_hop(report.hop_timeline)
    gpu_idle = compute_gpu_idle_pct_waiting_on_io(report.hop_timeline)
    return report.model_copy(
        update={
            "bottleneck_hop": bottleneck,
            "gpu_idle_pct_waiting_on_io": gpu_idle,
        }
    )
