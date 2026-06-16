"""Snapshot collect tests."""

from __future__ import annotations

from pathlib import Path

from nvme_sentinel.snapshot.collect import collect_snapshot, load_snapshot
from nvme_sentinel.telemetry.source import TelemetrySource


def test_collect_mock_snapshot(tmp_path: Path) -> None:
    snap = collect_snapshot("/dev/mock-nvme0", mock=True)
    assert snap.readonly is True
    assert snap.telemetry_source == TelemetrySource.MOCK
    assert snap.smart_health is not None
    assert snap.identify_controller_b64 is not None
    assert snap.smart_health_b64 is not None

    out = tmp_path / "snap.json"
    out.write_bytes(snap.model_dump_json(indent=2).encode("utf-8"))
    loaded = load_snapshot(str(out))
    assert loaded.device_path == snap.device_path
