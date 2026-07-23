"""Hardware health payloads for the OpenVault console."""

from __future__ import annotations

from typing import Any

from nvme_profiler.path_trace import build_mock_path_trace_report

from openmw.demo_payload import build_demo_payload


def devices_payload(
    *,
    use_live_detect: bool = True,
    model_id: str = "llama-3.3-8b",
) -> dict[str, Any]:
    """Return detection + device health cards (mock-safe)."""
    return build_demo_payload(model_id=model_id, use_live_detect=use_live_detect)


_HOP_LABELS: dict[str, str] = {
    "ssd_admin": "NVMe Admin",
    "driver_ioctl": "Driver IOCTL",
    "pcie_link": "PCIe Link",
    "cpu_copy": "CPU Copy",
    "host_ram": "Host DRAM",
    "ram_to_vram": "Host → VRAM",
    "gpu_compute": "GPU Compute",
}


def bottleneck_payload(*, device_path: str = "/dev/mock-nvme0") -> dict[str, Any]:
    """Return PathTrace bottleneck summary."""
    report = build_mock_path_trace_report(device_path=device_path)
    bottleneck = report.bottleneck_hop.value if report.bottleneck_hop else None
    hops = []
    for hop in report.hop_timeline:
        hop_id = hop.hop_id.value
        hops.append(
            {
                "hop_id": hop_id,
                "label": _HOP_LABELS.get(hop_id, hop_id),
                "duration_ms": hop.duration_ms,
                "bytes_moved": hop.bytes_moved,
                "is_bottleneck": hop_id == bottleneck,
            }
        )
    return {
        "device_path": report.env_manifest.device_path,
        "bottleneck_hop": bottleneck,
        "bottleneck_label": _HOP_LABELS.get(bottleneck or "", bottleneck),
        "hop_timeline": hops,
        "gpu_idle_pct_waiting_on_io": report.gpu_idle_pct_waiting_on_io,
    }
