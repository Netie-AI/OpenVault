"""Telemetry read and source labeling."""

from __future__ import annotations

from nvme_sentinel.telemetry.read import read_smart
from nvme_sentinel.telemetry.source import TelemetrySource


def test_read_smart_mock() -> None:
    result = read_smart(None, mock=True)
    assert result.source == TelemetrySource.MOCK
    assert result.smart is not None
    assert result.degraded is False
    assert result.smart.percentage_used == 3
