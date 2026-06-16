"""Windows storage inventory via PowerShell WMI/CIM."""

from __future__ import annotations

import json
import subprocess

from nvme_sentinel.inventory.models import InventoryDevice


def list_windows_devices() -> list[InventoryDevice]:
    """Enumerate physical disks via Get-PhysicalDisk + partition drive letters."""
    ps = (
        "Get-PhysicalDisk | ForEach-Object { "
        "$d = $_; "
        "$letters = @(Get-Partition -DiskNumber $d.DeviceId -ErrorAction SilentlyContinue | "
        "Where-Object { $_.DriveLetter } | ForEach-Object { $_.DriveLetter + ':' }); "
        "[PSCustomObject]@{ "
        "DeviceId=$d.DeviceId; "
        "FriendlyName=$d.FriendlyName; "
        "Model=$d.Model; "
        "SerialNumber=$d.SerialNumber; "
        "Size=$d.Size; "
        "MediaType=$d.MediaType; "
        "BusType=$d.BusType; "
        "DriveLetters=($letters -join ',') "
        "} "
        "} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    raw = json.loads(result.stdout)
    if isinstance(raw, dict):
        raw = [raw]

    devices: list[InventoryDevice] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        disk_id = row.get("DeviceId")
        if disk_id is None:
            continue
        letters_raw = str(row.get("DriveLetters") or "")
        letters = [x.strip() for x in letters_raw.split(",") if x.strip()]
        bus = str(row.get("BusType") or "")
        media = str(row.get("MediaType") or "")
        model = str(row.get("Model") or row.get("FriendlyName") or "")
        is_nvme = bus.upper() == "NVME" or "NVMe" in model
        suggested = ["device-io-control", "wmi"]
        if is_nvme:
            suggested = ["native-nvme", "device-io-control", "wmi"]
        elif bus.upper() in ("USB", "RAID"):
            suggested = ["wmi", "usb-bridge-degraded"]

        size_val = row.get("Size")
        size_bytes: int | None = None
        if size_val is not None:
            try:
                size_bytes = int(size_val)
            except (TypeError, ValueError):
                size_bytes = None

        devices.append(
            InventoryDevice(
                device_path=rf"\\.\PhysicalDrive{disk_id}",
                friendly_name=str(row.get("FriendlyName") or ""),
                model=model,
                serial=str(row.get("SerialNumber") or ""),
                size_bytes=size_bytes,
                media_type=media,
                bus_type=bus,
                is_nvme=is_nvme,
                drive_letters=letters,
                suggested_telemetry=suggested,
            )
        )
    return devices
