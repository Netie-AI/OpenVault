"""Build schema-backed demo JSON for the OpenMW liquid-glass WebUI."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from nvme_profiler.path_trace import build_mock_path_trace_report
from nvme_profiler.schema import HopId

from openmw.device_profile import DeviceProfile, detect
from openmw.model_router import ModelRouter

_HOP_LABELS: dict[str, str] = {
    HopId.SSD_ADMIN.value: "NVMe Admin",
    HopId.DRIVER_IOCTL.value: "Driver IOCTL",
    HopId.PCIE_LINK.value: "PCIe Link",
    HopId.CPU_COPY.value: "CPU Copy",
    HopId.HOST_RAM.value: "Host DRAM",
    HopId.RAM_TO_VRAM.value: "Host → VRAM",
    HopId.GPU_COMPUTE.value: "GPU Compute",
}

_DEMO_FALLBACK_PROFILE = DeviceProfile(
    gpu_name="GeForce RTX 4050 Laptop GPU",
    gpu_vram_gb=6.0,
    gpu_bandwidth_gbps=192.0,
    system_ram_gb=32.0,
    cpu_cores=12,
    nvme_model="Micron 3400 NVMe",
    nvme_seq_read_gbps=6.8,
    nvme_endurance_tbw=600.0,
    unified_memory=False,
    cpu_inference_mode=False,
)


class MiddlewareComparison(TypedDict, total=False):
    """Honest shape: no invented speedup until an A/B harness exists."""

    available: bool
    reason: str
    baseline_tok_s: float | None
    optimized_tok_s: float | None
    speedup_pct: float | None
    baseline_gpu_idle_pct: float | None
    optimized_gpu_idle_pct: float | None
    bottleneck_before: str
    bottleneck_after: str
    middleware_features: list[str]


# Hops that are still hardcoded constants in the mock path-trace (not live SSD timings).
_SYNTHETIC_HOPS = frozenset(
    {
        HopId.PCIE_LINK.value,
        HopId.CPU_COPY.value,
        HopId.HOST_RAM.value,
        HopId.RAM_TO_VRAM.value,
        HopId.GPU_COMPUTE.value,
    }
)


def _middleware_comparison(
    baseline_tok_s: float,
    gpu_idle_pct: float,
    bottleneck: str,
) -> MiddlewareComparison:
    """Do not invent a speedup percentage.

    The old formula always produced ≈ +23.4% from constants. That looked like
    telemetry. Until an A/B harness times the same prompt through baseline vs
    optimised paths, Middleware Gain is unavailable — blank beats a false claim.
    """
    del baseline_tok_s, gpu_idle_pct, bottleneck  # unused until harness exists
    return {
        "available": False,
        "reason": (
            "Middleware Gain needs an A/B harness (same prompt, measured tokens "
            "and wall time). No number is shown until then."
        ),
        "baseline_tok_s": None,
        "optimized_tok_s": None,
        "speedup_pct": None,
        "baseline_gpu_idle_pct": None,
        "optimized_gpu_idle_pct": None,
        "bottleneck_before": "",
        "bottleneck_after": "",
        "middleware_features": [],
    }


def _profile_to_dict(profile: DeviceProfile) -> dict[str, object]:
    return {
        "gpu_name": profile.gpu_name,
        "gpu_vram_gb": profile.gpu_vram_gb,
        "gpu_bandwidth_gbps": profile.gpu_bandwidth_gbps,
        "system_ram_gb": profile.system_ram_gb,
        "cpu_cores": profile.cpu_cores,
        "nvme_model": profile.nvme_model,
        "nvme_seq_read_gbps": profile.nvme_seq_read_gbps,
        "nvme_endurance_tbw": profile.nvme_endurance_tbw,
        "unified_memory": profile.unified_memory,
        "cpu_inference_mode": profile.cpu_inference_mode,
    }


def _device_cards(profile: DeviceProfile) -> list[dict[str, object]]:
    nvme_health = 94 if profile.nvme_seq_read_gbps >= 5.0 else 78
    gpu_health = 88 if profile.gpu_vram_gb >= 8.0 else 72
    return [
        {
            "kind": "nvme",
            "name": profile.nvme_model or "Unknown NVMe",
            "health_pct": nvme_health,
            "status": "good" if nvme_health >= 85 else "caution",
            "metric_label": "Seq Read",
            "metric_value": f"{profile.nvme_seq_read_gbps:.1f} GB/s",
            "secondary_label": "Endurance",
            "secondary_value": f"{profile.nvme_endurance_tbw:.0f} TBW",
            "telemetry": "native-nvme",
        },
        {
            "kind": "gpu",
            "name": profile.gpu_name or "No discrete GPU",
            "health_pct": gpu_health,
            "status": "good" if gpu_health >= 80 else "caution",
            "metric_label": "VRAM",
            "metric_value": f"{profile.gpu_vram_gb:.0f} GB",
            "secondary_label": "Bandwidth",
            "secondary_value": f"{profile.gpu_bandwidth_gbps:.0f} GB/s",
            "telemetry": "nvml",
        },
        {
            "kind": "cpu",
            "name": f"{profile.cpu_cores}-core host",
            "health_pct": 96,
            "status": "good",
            "metric_label": "System RAM",
            "metric_value": f"{profile.system_ram_gb:.0f} GB",
            "secondary_label": "Inference",
            "secondary_value": "GPU primary" if not profile.cpu_inference_mode else "CPU mode",
            "telemetry": "psutil",
        },
    ]


def build_demo_payload(
    *,
    model_id: str = "llama-3.3-8b",
    use_live_detect: bool = True,
    device_path: str = "/dev/nvme0n1",
) -> dict[str, object]:
    """Assemble WebUI payload from live or fallback hardware + mock path trace.

    ``demo_mode`` reports whether the *profile actually served* is fabricated —
    not merely which branch the caller asked for. Those are different questions
    whenever live detection runs and comes back empty, and conflating them is
    how a machine with an AMD iGPU and a Samsung SSD ended up rendering
    "LIVE · GeForce RTX 4050 · Micron 3400". Substituted values must never
    inherit a live badge.
    """
    profile = detect() if use_live_detect else _DEMO_FALLBACK_PROFILE
    substituted = False
    if profile.gpu_name is None and profile.nvme_model is None:
        # Detection ran and learned nothing identifying. Keep the real numbers
        # we *did* measure (RAM, cores) rather than swapping the whole profile
        # for someone else's laptop.
        profile = replace(
            _DEMO_FALLBACK_PROFILE,
            system_ram_gb=profile.system_ram_gb,
            cpu_cores=profile.cpu_cores,
            cpu_inference_mode=profile.cpu_inference_mode,
        )
        substituted = True

    path_report = build_mock_path_trace_report(
        device_path=profile.nvme_model or device_path,
    )
    router = ModelRouter()
    routing = router.route(profile, model_id)

    bottleneck = (
        path_report.bottleneck_hop.value if path_report.bottleneck_hop else HopId.RAM_TO_VRAM.value
    )
    gpu_idle = path_report.gpu_idle_pct_waiting_on_io or 27.9

    hop_timeline = [
        {
            "hop_id": hop.hop_id.value,
            "label": _HOP_LABELS.get(hop.hop_id.value, hop.hop_id.value),
            "duration_ms": round(hop.duration_ms, 2),
            "bytes_moved": hop.bytes_moved,
            "notes": hop.notes or "",
            "is_bottleneck": hop.hop_id.value == bottleneck,
            # GPU-side hops are still constants; SSD-side can become live later.
            "is_synthetic": hop.hop_id.value in _SYNTHETIC_HOPS,
        }
        for hop in path_report.hop_timeline
    ]

    data_flow = [
        {
            "id": hop["hop_id"],
            "label": hop["label"],
            "duration_ms": hop["duration_ms"],
            "active": True,
            "is_bottleneck": hop["is_bottleneck"],
            "is_synthetic": hop["is_synthetic"],
        }
        for hop in hop_timeline
    ]

    middleware = _middleware_comparison(routing.estimated_tok_s, gpu_idle, bottleneck)

    return {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "demo_mode": (not use_live_detect) or substituted,
        # Why the payload is (or is not) trustworthy, so the UI can say so
        # instead of showing a bare LIVE pill over substituted hardware.
        "profile_source": (
            "fallback_flag"
            if not use_live_detect
            else "fallback_substituted"
            if substituted
            else "live"
        ),
        "profile_degraded_reason": (
            "Hardware detection returned no GPU and no NVMe identity, so the "
            "displayed GPU/NVMe are placeholders. RAM and CPU cores are real."
            if substituted
            else None
        ),
        "profile": _profile_to_dict(profile),
        "devices": _device_cards(profile),
        "path_trace": {
            "hop_timeline": hop_timeline,
            "bottleneck_hop": bottleneck,
            "bottleneck_label": _HOP_LABELS.get(bottleneck, bottleneck),
            "gpu_idle_pct_waiting_on_io": gpu_idle,
        },
        "routing": asdict(routing),
        "middleware_comparison": middleware,
        "data_flow": data_flow,
    }


def write_demo_payload(
    output_dir: Path,
    *,
    model_id: str = "llama-3.3-8b",
    use_live_detect: bool = True,
) -> Path:
    """Write demo.json sample payload to ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_demo_payload(model_id=model_id, use_live_detect=use_live_detect)
    out_path = output_dir / "demo.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def openvault_app_dir() -> Path:
    """Return path to the Next.js OpenVault app (``apps/web``)."""
    # openmw/demo_payload.py → OpenMW/ → OpenVault/
    return Path(__file__).resolve().parents[2] / "apps" / "web"
