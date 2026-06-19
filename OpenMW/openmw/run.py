"""Offload measurement loop: snapshot → workload → snapshot → reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog

from nvme_profiler.path_trace import build_mock_path_trace_report
from nvme_profiler.report import save_path_trace_report
from nvme_sentinel.bench.run import build_bench_run_report
from nvme_sentinel.bench.report import save_bench_run_report
from nvme_sentinel.snapshot.schema import DeviceSnapshot
from nvme_sentinel.telemetry.source import TelemetrySource

from openmw.prefetch_naive import NaivePrefetchConfig
from openmw.prefetch_heuristic import HeuristicPrefetchConfig, lmcache_heuristic_overlay
from openmw.prefetch_naive import lmcache_disk_config

log = structlog.get_logger()


@dataclass(frozen=True)
class OffloadRunResult:
    """Artifacts from one offload measurement loop."""

    bench_html: Path
    path_trace_html: Path
    prefetch_enabled: bool
    heuristic_enabled: bool
    manifest_path: Path | None


def _mock_snapshot(device_path: str, duw: int) -> DeviceSnapshot:
    return DeviceSnapshot(
        device_path=device_path,
        platform="mock",
        telemetry_source=TelemetrySource.MOCK,
        smart_health={"data_units_written": duw},
    )


def run_offload_measurement_loop(
    output_dir: Path,
    *,
    device_path: str = "/dev/mock-nvme0",
    prefetch: NaivePrefetchConfig | None = None,
    heuristic: HeuristicPrefetchConfig | None = None,
    mock_workload_du_delta: int = 1000,
) -> OffloadRunResult:
    """Run baseline → offload (mock) → after snapshot; emit BenchRunReport + PathTraceReport."""
    output_dir.mkdir(parents=True, exist_ok=True)
    prefetch_cfg = prefetch or NaivePrefetchConfig(enabled=False)
    heuristic_cfg = heuristic or HeuristicPrefetchConfig(enabled=False)

    before = _mock_snapshot(device_path, 0)
    after = _mock_snapshot(device_path, mock_workload_du_delta)

    bench_html = output_dir / "bench_offload.html"
    bench_report = build_bench_run_report(
        device_path,
        before,
        after,
        enclosure_class="mock-offload",
    )
    save_bench_run_report(bench_report, bench_html)

    path_report = build_mock_path_trace_report(device_path=device_path)
    path_html = output_dir / "path_trace_offload.html"
    save_path_trace_report(path_report, path_html)

    lmcache_cfg = lmcache_disk_config(prefetch_cfg, str(output_dir / "kv_cache"))
    if heuristic_cfg.enabled:
        lmcache_cfg = lmcache_heuristic_overlay(lmcache_cfg, heuristic_cfg)

    manifest_path = output_dir / "offload_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "device_path": device_path,
                "lmcache_config": lmcache_cfg,
                "prefetch_enabled": prefetch_cfg.enabled,
                "heuristic_enabled": heuristic_cfg.enabled,
                "gpu_idle_pct_waiting_on_io": path_report.gpu_idle_pct_waiting_on_io,
                "bottleneck_hop": (
                    path_report.bottleneck_hop.value if path_report.bottleneck_hop else None
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info(
        "offload_measurement_complete",
        bench_html=str(bench_html),
        path_trace_html=str(path_html),
        prefetch=prefetch_cfg.enabled,
    )
    return OffloadRunResult(
        bench_html=bench_html,
        path_trace_html=path_html,
        prefetch_enabled=prefetch_cfg.enabled,
        heuristic_enabled=heuristic_cfg.enabled,
        manifest_path=manifest_path,
    )


def compare_prefetch_runs(
    output_dir: Path, device_path: str = "/dev/mock-nvme0"
) -> dict[str, object]:
    """Run prefetch off/on and return comparison metrics for Q4 gate."""
    off_dir = output_dir / "prefetch_off"
    on_dir = output_dir / "prefetch_on"
    off = run_offload_measurement_loop(
        off_dir, device_path=device_path, prefetch=NaivePrefetchConfig(enabled=False)
    )
    on = run_offload_measurement_loop(
        on_dir,
        device_path=device_path,
        prefetch=NaivePrefetchConfig(enabled=True, prefetch_blocks=4),
    )
    off_manifest = json.loads((off.manifest_path or Path()).read_text(encoding="utf-8"))
    on_manifest = json.loads((on.manifest_path or Path()).read_text(encoding="utf-8"))
    return {
        "prefetch_off_gpu_idle_pct": off_manifest.get("gpu_idle_pct_waiting_on_io"),
        "prefetch_on_gpu_idle_pct": on_manifest.get("gpu_idle_pct_waiting_on_io"),
        "prefetch_off_bottleneck": off_manifest.get("bottleneck_hop"),
        "prefetch_on_bottleneck": on_manifest.get("bottleneck_hop"),
    }
