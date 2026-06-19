"""Path trace and fusion unit tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from nvme_profiler.fuse import (
    compute_bottleneck_hop,
    compute_gpu_idle_pct_waiting_on_io,
    enrich_path_trace,
    fuse_admin_timings,
)
from nvme_profiler.path_trace import build_mock_path_trace_report, build_path_trace_report
from nvme_profiler.report import render_path_trace_report
from nvme_profiler.schema import HopId, HopRecord, PathTraceEnvManifest, PathTraceReport


def test_fuse_admin_timings() -> None:
    records = [{"duration_ms": 3, "data_len": 512, "adapter": "Mock"}]
    timeline = fuse_admin_timings(records)
    assert len(timeline) == 1
    assert timeline[0].hop_id == HopId.SSD_ADMIN


def test_compute_bottleneck_hop() -> None:
    timeline = [
        HopRecord(hop_id=HopId.SSD_ADMIN, start_ts=0, end_ts=0.01, duration_ms=10, bytes_moved=512),
        HopRecord(
            hop_id=HopId.RAM_TO_VRAM,
            start_ts=0.01,
            end_ts=0.11,
            duration_ms=100,
            bytes_moved=67_108_864,
        ),
    ]
    assert compute_bottleneck_hop(timeline) == HopId.RAM_TO_VRAM


def test_gpu_idle_pct() -> None:
    timeline = [
        HopRecord(hop_id=HopId.SSD_ADMIN, start_ts=0, end_ts=0.05, duration_ms=50, bytes_moved=512),
        HopRecord(
            hop_id=HopId.GPU_COMPUTE,
            start_ts=0.05,
            end_ts=0.15,
            duration_ms=100,
            bytes_moved=0,
        ),
    ]
    pct = compute_gpu_idle_pct_waiting_on_io(timeline)
    assert pct is not None
    assert 30.0 < pct < 35.0


@patch("nvme_profiler.path_trace.run_capability_probe")
def test_build_mock_path_trace_report(mock_probe: object) -> None:
    from nvme_profiler.schema import CapabilityManifest

    mock_probe.return_value = CapabilityManifest(os="linux", python_version="3.12.0")
    report = build_mock_path_trace_report()
    assert report.bottleneck_hop is not None
    assert report.gpu_idle_pct_waiting_on_io is not None
    assert len(report.hop_timeline) >= 3


def test_path_trace_html(tmp_path: Path) -> None:
    env = PathTraceEnvManifest(platform="linux", python_version="3.12.0")
    report = enrich_path_trace(
        PathTraceReport(
            env_manifest=env,
            hop_timeline=[
                HopRecord(
                    hop_id=HopId.SSD_ADMIN,
                    start_ts=0,
                    end_ts=0.002,
                    duration_ms=2,
                    bytes_moved=512,
                )
            ],
        )
    )
    out = tmp_path / "trace.html"
    render_path_trace_report(report, out)
    text = out.read_text(encoding="utf-8")
    assert "Full-Path Trace" in text
    assert "ssd_admin" in text


@patch("nvme_profiler.path_trace.run_capability_probe")
def test_build_path_trace_with_nsys_export(mock_probe: object, tmp_path: Path) -> None:
    from nvme_profiler.schema import CapabilityManifest

    mock_probe.return_value = CapabilityManifest(os="linux", python_version="3.12.0")
    export = tmp_path / "nsys.json"
    export.write_text(
        '[{"name": "cudaMemcpyAsync H2D", "start": 1000000, "end": 31000000, "bytes": 1048576}]',
        encoding="utf-8",
    )
    report = build_path_trace_report(
        [{"duration_ms": 1, "data_len": 512, "adapter": "Mock"}],
        nsys_export=export,
    )
    assert any(h.hop_id == HopId.RAM_TO_VRAM for h in report.hop_timeline)
