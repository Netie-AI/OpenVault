"""Bench run orchestration helpers."""

from __future__ import annotations

from pathlib import Path

from nvme_sentinel.bench.report import save_bench_run_report
from nvme_sentinel.bench.schema import (
    BenchRunReport,
    build_env_manifest,
    compute_wear_delta,
)
from nvme_sentinel.snapshot.collect import collect_snapshot
from nvme_sentinel.snapshot.schema import DeviceSnapshot
from nvme_sentinel.stress.parser import StressResult


def build_bench_run_report(
    device_path: str,
    snapshot_before: DeviceSnapshot,
    snapshot_after: DeviceSnapshot,
    *,
    stress_result: StressResult | None = None,
    enclosure_class: str = "unknown",
    html_output: Path | None = None,
) -> BenchRunReport:
    """Assemble BenchRunReport from paired snapshots and optional stress result."""
    env = build_env_manifest(
        device_path,
        enclosure_class=enclosure_class,
        stress_tool=stress_result.tool if stress_result else None,
        profile_name=stress_result.profile_name if stress_result else None,
    )
    wear = compute_wear_delta(snapshot_before, snapshot_after)
    report = BenchRunReport(
        env_manifest=env,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
        stress_result=stress_result,
        wear_delta=wear,
    )
    if html_output is not None:
        return save_bench_run_report(report, html_output)
    return report


def collect_before_after_mock(
    device_path: str = "/dev/mock-nvme0",
) -> tuple[DeviceSnapshot, DeviceSnapshot]:
    """Collect paired mock snapshots for demo/tests."""
    before = collect_snapshot(device_path, mock=True)
    after = collect_snapshot(device_path, mock=True)
    return before, after
