"""Unified SMART read with telemetry source labeling."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from nvme_sentinel.commands.log_pages import get_smart_health
from nvme_sentinel.hal.exceptions import AdminCommandError, PermissionDenied
from nvme_sentinel.hal.factory import get_adapter
from nvme_sentinel.hal.interface import StorageInterface
from nvme_sentinel.models.smart import SmartHealthLog
from nvme_sentinel.telemetry.source import TelemetrySource


@dataclass(frozen=True, slots=True)
class SmartReadResult:
    """Outcome of a read-only SMART/health query."""

    source: TelemetrySource
    smart: SmartHealthLog | None
    wmi_counters: dict[str, int | str] | None
    degraded: bool
    message: str | None = None


def _source_for_adapter(dev: StorageInterface, mock: bool) -> TelemetrySource:
    if mock:
        return TelemetrySource.MOCK
    caps = dev.capabilities()
    if "mock" in caps:
        return TelemetrySource.MOCK
    if "ioctl" in caps:
        return TelemetrySource.IOCTL
    if "device-io-control" in caps:
        return TelemetrySource.DEVICE_IO_CONTROL
    if "host-proxy" in caps:
        return TelemetrySource.HOST_PROXY
    return TelemetrySource.NATIVE_NVME


def read_smart(
    device_path: str | None,
    *,
    mock: bool = False,
    force: str | None = None,
) -> SmartReadResult:
    """
    Read SMART via native passthrough; on Windows fall back to WMI when blocked.

    Read-only: no admin commands that modify media or firmware.
    """
    from nvme_sentinel.hal.factory import AdapterForce

    adapter_force: AdapterForce | None
    if force in ("linux", "windows", "mock", "host-proxy"):
        adapter_force = force  # type: ignore[assignment]
    else:
        adapter_force = "mock" if mock else None
    with get_adapter(device_path=device_path, force=adapter_force) as dev:
        try:
            smart = get_smart_health(dev)
            src = _source_for_adapter(dev, mock)
            return SmartReadResult(
                source=src,
                smart=smart,
                wmi_counters=None,
                degraded=False,
            )
        except (PermissionDenied, AdminCommandError) as exc:
            is_ioctl_unsupported = (
                isinstance(exc, AdminCommandError) and exc.status_code == 1
            )
            if sys.platform == "win32" and device_path and (
                isinstance(exc, PermissionDenied) or is_ioctl_unsupported
            ):
                from nvme_sentinel.adapters._wmi_fallback import (
                    disk_number_from_path,
                    get_reliability_counters,
                )

                counters = get_reliability_counters(disk_number_from_path(device_path))
                reason = (
                    "IOCTL_STORAGE_PROTOCOL_COMMAND not supported by driver"
                    if is_ioctl_unsupported
                    else "permission denied for passthrough"
                )
                return SmartReadResult(
                    source=TelemetrySource.WMI,
                    smart=None,
                    wmi_counters=counters,
                    degraded=True,
                    message=reason,
                )
            raise
