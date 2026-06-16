"""Telemetry source labels and read helpers."""

from nvme_sentinel.telemetry.read import SmartReadResult, read_smart
from nvme_sentinel.telemetry.source import TelemetrySource

__all__ = ["SmartReadResult", "TelemetrySource", "read_smart"]
