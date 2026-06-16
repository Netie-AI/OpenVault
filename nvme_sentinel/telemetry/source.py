"""How storage health data was obtained (read-only telemetry paths)."""

from __future__ import annotations

from enum import Enum


class TelemetrySource(str, Enum):
    """Label for the transport/path that supplied device telemetry."""

    NATIVE_NVME = "native-nvme"
    IOCTL = "ioctl"
    DEVICE_IO_CONTROL = "device-io-control"
    WMI = "wmi"
    NVME_CLI = "nvme-cli"
    SMARTCTL = "smartctl"
    MOCK = "mock"
    HOST_PROXY = "host-proxy"
    UNRAID = "unraid"
    USB_BRIDGE_DEGRADED = "usb-bridge-degraded"

    def label(self) -> str:
        """Human-readable label for CLI and reports."""
        return _LABELS.get(self, self.value)


_NATIVE_PASSTHROUGH: frozenset[TelemetrySource] = frozenset(
    {
        TelemetrySource.NATIVE_NVME,
        TelemetrySource.IOCTL,
        TelemetrySource.DEVICE_IO_CONTROL,
    }
)


def is_native_passthrough(source: TelemetrySource) -> bool:
    """True when NVMe SMART log 0x02 (data_units_written) may be available."""
    return source in _NATIVE_PASSTHROUGH


_LABELS: dict[TelemetrySource, str] = {
    TelemetrySource.NATIVE_NVME: "Full NVMe admin passthrough (512-byte SMART)",
    TelemetrySource.IOCTL: "Linux NVMe ioctl passthrough",
    TelemetrySource.DEVICE_IO_CONTROL: "Windows DeviceIoControl NVMe passthrough",
    TelemetrySource.WMI: "Windows WMI MSFT_StorageReliabilityCounter (subset)",
    TelemetrySource.NVME_CLI: "nvme-cli subprocess fallback",
    TelemetrySource.SMARTCTL: "smartctl JSON (ATA/NVMe)",
    TelemetrySource.MOCK: "MockNvmeAdapter fixtures",
    TelemetrySource.HOST_PROXY: "Host-exported read-only snapshot",
    TelemetrySource.UNRAID: "Unraid remote collector (SSH)",
    TelemetrySource.USB_BRIDGE_DEGRADED: "USB bridge — degraded telemetry only",
}
