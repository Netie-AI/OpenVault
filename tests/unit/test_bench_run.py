"""BenchRunReport schema and wear delta tests."""

from __future__ import annotations

import json
from pathlib import Path

from nvme_sentinel.bench import build_bench_run_report, collect_before_after_mock
from nvme_sentinel.bench.report import render_bench_run_report
from nvme_sentinel.bench.schema import (
    BenchRunReport,
    WearDelta,
    build_env_manifest,
    compute_wear_delta,
)
from nvme_sentinel.snapshot.schema import DeviceSnapshot
from nvme_sentinel.stress.fio import parse_fio_json
from nvme_sentinel.telemetry.source import TelemetrySource

_KNOWN_DUW_DELTA = 1_953_125  # -> 1000.00 GB / 1.000000 TB host bytes


def _snapshot_with_duw(data_units_written: int) -> DeviceSnapshot:
    return DeviceSnapshot(
        device_path="/dev/mock-nvme0",
        platform="linux",
        telemetry_source=TelemetrySource.MOCK,
        smart_health={"data_units_written": data_units_written},
    )


def test_compute_wear_delta_from_mock_snapshots() -> None:
    before, after = collect_before_after_mock()
    delta = compute_wear_delta(before, after)
    assert isinstance(delta, WearDelta)
    assert delta.data_units_written_delta >= 0


def test_build_bench_run_report_html(tmp_path: Path) -> None:
    before = _snapshot_with_duw(0)
    after = _snapshot_with_duw(_KNOWN_DUW_DELTA)
    raw = json.loads(Path("tests/fixtures/fio_output_sample.json").read_text(encoding="utf-8"))
    stress = parse_fio_json(raw, "rand_read_4k")
    html_out = tmp_path / "bench.html"
    report = build_bench_run_report(
        "/dev/mock-nvme0",
        before,
        after,
        stress_result=stress,
        enclosure_class="mock",
        html_output=html_out,
    )
    assert report.html_path == str(html_out)
    assert html_out.exists()
    text = html_out.read_text(encoding="utf-8")
    assert "Wear Accounting" in text
    assert "Host writes this run" in text
    assert "1000.00 GB" in text
    assert "1.000000 TB" in text
    assert f"{_KNOWN_DUW_DELTA:,} data units written" in text
    assert "GiB" not in text
    assert "Degraded telemetry" not in text


def test_bench_report_shows_degraded_banner_for_wmi(tmp_path: Path) -> None:
    before = DeviceSnapshot(
        device_path=r"\\.\PhysicalDrive1",
        platform="win32",
        telemetry_source=TelemetrySource.WMI,
        smart_health=None,
        wmi_fallback={"Wear": 0},
    )
    after = before.model_copy()
    report = BenchRunReport(
        env_manifest=build_env_manifest(
            r"\\.\PhysicalDrive1",
            enclosure_class="usb-bridge",
        ),
        snapshot_before=before,
        snapshot_after=after,
        wear_delta=compute_wear_delta(before, after),
    )
    html_out = tmp_path / "bench_wmi.html"
    render_bench_run_report(report, html_out)
    text = html_out.read_text(encoding="utf-8")
    assert "Degraded telemetry" in text
    assert "wear delta unavailable" in text
    assert "WMI" in text or "MSFT_StorageReliabilityCounter" in text
