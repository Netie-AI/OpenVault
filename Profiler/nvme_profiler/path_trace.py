"""Build PathTraceReport from admin timings and optional nsys capture."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from nvme_profiler.fuse import enrich_path_trace, fuse_admin_timings
from nvme_profiler.nsys import mock_nsys_hops, nsys_version, parse_nsys_export_json
from nvme_profiler.probe import run_capability_probe
from nvme_profiler.schema import PathTraceEnvManifest, PathTraceReport


def build_path_trace_report(
    admin_records: list[dict[str, object]],
    *,
    device_path: str = "",
    nsys_export: Path | None = None,
    use_mock_nsys: bool = False,
) -> PathTraceReport:
    """Assemble PathTraceReport from SSD admin timings and optional nsys export."""
    manifest = run_capability_probe()
    nsys_hops = None
    if nsys_export is not None:
        nsys_hops = parse_nsys_export_json(nsys_export)
    elif use_mock_nsys:
        nsys_hops = mock_nsys_hops()
    timeline = fuse_admin_timings(admin_records, nsys_hops)
    env = PathTraceEnvManifest(
        collected_at=datetime.now(timezone.utc),
        platform=sys.platform,
        python_version=sys.version.split()[0],
        kernel=manifest.kernel,
        device_path=device_path,
        nsys_version=nsys_version(),
        capability_manifest=manifest,
    )
    report = PathTraceReport(env_manifest=env, hop_timeline=timeline)
    return enrich_path_trace(report)


def build_mock_path_trace_report(device_path: str = "/dev/mock-nvme0") -> PathTraceReport:
    """Mock path trace for CI and demos without GPU/nsys."""
    admin_records: list[dict[str, object]] = [
        {"duration_ms": 2.5, "data_len": 512, "adapter": "MockNvmeAdapter", "opcode": 2},
        {"duration_ms": 1.2, "data_len": 4096, "adapter": "MockNvmeAdapter", "opcode": 6},
    ]
    return build_path_trace_report(
        admin_records,
        device_path=device_path,
        use_mock_nsys=True,
    )
