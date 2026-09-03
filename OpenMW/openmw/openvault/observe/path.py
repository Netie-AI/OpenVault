"""Path-trace observe payloads — prefer live PathTrace, fall back to mock.

The live report is fused from ``OPENVAULT_HOME/last_admin_timings.json``, which
``POST /api/observe/trace`` writes. Two rules keep this honest:

* records emitted by the mock adapter never qualify as live, so a stale or
  hand-placed fixture file cannot make the timeline render with a live badge;
* the GPU-side hops come from ``nvme_profiler.nsys``, which substitutes synthetic
  hops when no Nsight Systems export exists — so each hop carries its own
  ``source`` and the payload says which halves are real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nvme_profiler.path_trace import build_mock_path_trace_report, build_path_trace_report
from nvme_profiler.schema import PathTraceReport

from openmw.openvault.observe.api_models import HOP_LABELS, hop_reach_label
from openmw.openvault.observe.hotspot import hop_severity

TIMINGS_FILENAME = "last_admin_timings.json"
NSYS_EXPORT_FILENAME = "last_nsys_export.json"


def timings_path() -> Path:
    """Location of the admin-timing capture that promotes this report to live."""
    from openmw.openvault.paths import ensure_home

    return ensure_home() / TIMINGS_FILENAME


def nsys_export_path() -> Path:
    """Location of the optional Nsight Systems export for the GPU-side hops."""
    from openmw.openvault.paths import ensure_home

    return ensure_home() / NSYS_EXPORT_FILENAME


def _hop_source(notes: str) -> str:
    # Admin hops carry the adapter class name; nsys hops carry the event name,
    # and nvme_profiler.nsys.mock_nsys_hops prefixes its synthetic ones with "mock".
    return "mock" if "mock" in notes.lower() else "live"


def report_to_payload(
    report: PathTraceReport,
    *,
    source: str,
    degraded_reason: str | None = None,
) -> dict[str, Any]:
    bottleneck = report.bottleneck_hop.value if report.bottleneck_hop else None
    hops: list[dict[str, Any]] = []
    mock_hops: list[str] = []
    for hop in report.hop_timeline:
        hop_id = hop.hop_id.value
        is_bn = hop_id == bottleneck
        severity = hop_severity(hop, bottleneck_id=bottleneck)
        hop_src = _hop_source(hop.notes)
        if hop_src == "mock":
            mock_hops.append(hop_id)
        hops.append(
            {
                "hop_id": hop_id,
                "label": HOP_LABELS.get(hop_id, hop_id),
                "duration_ms": hop.duration_ms,
                "bytes_moved": hop.bytes_moved,
                "is_bottleneck": is_bn,
                "severity": severity,
                "reach": hop_reach_label(is_bottleneck=is_bn, severity=severity),
                "source": hop_src,
                "notes": hop.notes,
            }
        )

    if source == "live" and mock_hops and degraded_reason is None:
        simulated = ", ".join(HOP_LABELS.get(h, h) for h in dict.fromkeys(mock_hops))
        degraded_reason = (
            f"SSD-side timings are real; {simulated} are simulated — no Nsight "
            f"Systems export at {nsys_export_path()}"
        )

    return {
        "device_path": report.env_manifest.device_path,
        "bottleneck_hop": bottleneck,
        "bottleneck_label": HOP_LABELS.get(bottleneck or "", bottleneck),
        "hop_timeline": hops,
        "gpu_idle_pct_waiting_on_io": report.gpu_idle_pct_waiting_on_io,
        "source": source,
        "degraded": degraded_reason is not None,
        "degraded_reason": degraded_reason,
        "mock_hops": list(dict.fromkeys(mock_hops)),
        "observe": True,
    }


def _load_admin_records() -> tuple[list[dict[str, object]], str | None]:
    """Read captured admin timings, rejecting anything the mock adapter produced."""
    path = timings_path()
    if not path.is_file():
        return [], (
            f"no admin-command capture yet — POST /api/observe/trace writes {path}, "
            "which needs NVMe passthrough (Administrator on Windows)"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"admin-timing capture at {path} is unreadable: {exc}"

    records = raw if isinstance(raw, list) else raw.get("admin_command_timing", [])
    if not isinstance(records, list) or not records:
        return [], f"admin-timing capture at {path} contains no records"

    typed = [dict(r) for r in records if isinstance(r, dict)]
    if not typed:
        return [], f"admin-timing capture at {path} contains no usable records"
    if all("mock" in str(r.get("adapter", "")).lower() for r in typed):
        # The whole point of the badge: fixtures must never be promoted to live.
        return [], (
            f"admin-timing capture at {path} came from the mock adapter — "
            "it cannot be reported as live"
        )
    return typed, None


def _try_live_report(device_path: str) -> tuple[PathTraceReport | None, str | None]:
    """Fuse a live report from captured admin timings; return (report, why_not)."""
    records, reason = _load_admin_records()
    if not records:
        return None, reason
    try:
        export = nsys_export_path()
        nsys_export = export if export.is_file() else None
        report = build_path_trace_report(
            records,
            device_path=device_path,
            nsys_export=nsys_export,
            use_mock_nsys=nsys_export is None,
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return None, f"path-trace fusion failed: {exc}"
    return report, None


def observe_path_payload(
    *,
    device_path: str = "/dev/mock-nvme0",
    prefer_live: bool = True,
) -> dict[str, Any]:
    """Return path timeline with severity for red-hotspot UI."""
    fallback_reason: str | None = None
    if prefer_live:
        live, fallback_reason = _try_live_report(device_path)
        if live is not None:
            return report_to_payload(live, source="live")
    else:
        fallback_reason = "live trace not requested"
    report = build_mock_path_trace_report(device_path=device_path)
    return report_to_payload(report, source="mock", degraded_reason=fallback_reason)


def bottleneck_payload(*, device_path: str = "/dev/mock-nvme0") -> dict[str, Any]:
    """Backward-compatible alias used by /api/health/bottleneck."""
    return observe_path_payload(device_path=device_path, prefer_live=True)
