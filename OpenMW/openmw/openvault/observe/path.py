"""Path-trace observe payloads — prefer live PathTrace, fall back to mock."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nvme_profiler.path_trace import build_mock_path_trace_report, build_path_trace_report
from nvme_profiler.schema import PathTraceReport

from openmw.openvault.observe.api_models import HOP_LABELS, hop_reach_label
from openmw.openvault.observe.hotspot import hop_severity


def _report_to_payload(report: PathTraceReport, *, source: str) -> dict[str, Any]:
    bottleneck = report.bottleneck_hop.value if report.bottleneck_hop else None
    hops: list[dict[str, Any]] = []
    for hop in report.hop_timeline:
        hop_id = hop.hop_id.value
        is_bn = hop_id == bottleneck
        severity = hop_severity(hop, bottleneck_id=bottleneck)
        hops.append(
            {
                "hop_id": hop_id,
                "label": HOP_LABELS.get(hop_id, hop_id),
                "duration_ms": hop.duration_ms,
                "bytes_moved": hop.bytes_moved,
                "is_bottleneck": is_bn,
                "severity": severity,
                "reach": hop_reach_label(is_bottleneck=is_bn, severity=severity),
            }
        )
    return {
        "device_path": report.env_manifest.device_path,
        "bottleneck_hop": bottleneck,
        "bottleneck_label": HOP_LABELS.get(bottleneck or "", bottleneck),
        "hop_timeline": hops,
        "gpu_idle_pct_waiting_on_io": report.gpu_idle_pct_waiting_on_io,
        "source": source,
        "observe": True,
    }


def _try_live_report(device_path: str) -> PathTraceReport | None:
    """Build a live-ish report when admin timing JSON exists under OPENVAULT_HOME."""
    from openmw.openvault.paths import ensure_home

    timing_path = ensure_home() / "last_admin_timings.json"
    if not timing_path.is_file():
        return None
    try:
        import json

        raw = json.loads(timing_path.read_text(encoding="utf-8"))
        records = raw if isinstance(raw, list) else raw.get("admin_command_timing", [])
        if not isinstance(records, list) or not records:
            return None
        typed: list[dict[str, object]] = [dict(r) for r in records if isinstance(r, dict)]
        nsys_export: Path | None = None
        nsys_path = ensure_home() / "last_nsys_export.json"
        if nsys_path.is_file():
            nsys_export = nsys_path
        return build_path_trace_report(
            typed,
            device_path=device_path,
            nsys_export=nsys_export,
            use_mock_nsys=nsys_export is None,
        )
    except (OSError, ValueError, TypeError, KeyError):
        return None


def observe_path_payload(
    *,
    device_path: str = "/dev/mock-nvme0",
    prefer_live: bool = True,
) -> dict[str, Any]:
    """Return path timeline with severity for red-hotspot UI."""
    if prefer_live:
        live = _try_live_report(device_path)
        if live is not None:
            return _report_to_payload(live, source="live")
    report = build_mock_path_trace_report(device_path=device_path)
    return _report_to_payload(report, source="mock")


def bottleneck_payload(*, device_path: str = "/dev/mock-nvme0") -> dict[str, Any]:
    """Backward-compatible alias used by /api/health/bottleneck."""
    return observe_path_payload(device_path=device_path, prefer_live=True)
