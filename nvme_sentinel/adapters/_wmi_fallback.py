"""WMI MSFT_StorageReliabilityCounter fallback — no admin required (Windows)."""

from __future__ import annotations

import json
import re
import subprocess

from nvme_sentinel.hal.exceptions import CapabilityError


def get_reliability_counters(disk_number: int) -> dict[str, int | str]:
    """
    Query MSFT_StorageReliabilityCounter via PowerShell WMI.
    No admin required. Returns dict with subset of SMART fields.
    Fields available: Temperature, Wear, ReadErrorsTotal,
    WriteErrorsTotal, PowerOnHours, StartStopCycleCount.
    """
    ps_cmd = (
        f"Get-PhysicalDisk | Where-Object DeviceId -eq {disk_number} | "
        "Get-StorageReliabilityCounter | "
        "Select-Object Temperature,Wear,ReadErrorsTotal,"
        "WriteErrorsTotal,PowerOnHours,StartStopCycleCount | "
        "ConvertTo-Json"
    )
    result = subprocess.run(
        ["powershell", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise CapabilityError(
            f"WMI MSFT_StorageReliabilityCounter unavailable for disk {disk_number}"
        )
    raw: object = json.loads(result.stdout)
    if isinstance(raw, list):
        if not raw:
            raise CapabilityError(
                f"WMI MSFT_StorageReliabilityCounter empty for disk {disk_number}"
            )
        raw = raw[0]
    if not isinstance(raw, dict):
        raise CapabilityError(f"WMI MSFT_StorageReliabilityCounter bad JSON for disk {disk_number}")
    out: dict[str, int | str] = {}
    for key, val in raw.items():
        k = str(key)
        if val is None:
            continue
        if isinstance(val, bool):
            out[k] = str(val)
        elif isinstance(val, int):
            out[k] = val
        elif isinstance(val, float):
            out[k] = int(val) if val.is_integer() else str(val)
        else:
            out[k] = str(val)
    return out


def disk_number_from_path(path: str) -> int:
    """Extract disk number from \\\\.\\PhysicalDrive2 -> 2."""
    m = re.search(r"(\d+)$", path)
    if not m:
        raise ValueError(f"Cannot parse disk number from {path!r}")
    return int(m.group(1))
