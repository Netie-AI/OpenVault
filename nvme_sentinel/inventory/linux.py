"""Linux storage inventory via lsblk JSON."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from nvme_sentinel.inventory.models import InventoryDevice


_NVME_NS_RE = re.compile(r"^(nvme\d+)n\d+$")


def _nvme_char_device(block_name: str) -> str | None:
    """Map nvme0n1 -> /dev/nvme0 for ioctl admin passthrough."""
    if not block_name.startswith("nvme"):
        return None
    match = _NVME_NS_RE.match(block_name)
    if match is not None:
        return f"/dev/{match.group(1)}"
    return f"/dev/{block_name}"


def list_linux_devices() -> list[InventoryDevice]:
    """Enumerate block devices with lsblk -J."""
    result = subprocess.run(
        [
            "lsblk",
            "-J",
            "-o",
            "NAME,SIZE,TYPE,MODEL,SERIAL,TRAN,ROTA,MOUNTPOINT",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return _fallback_sysfs()

    data = json.loads(result.stdout)
    blockdevices = data.get("blockdevices")
    if not isinstance(blockdevices, list):
        return _fallback_sysfs()

    devices: list[InventoryDevice] = []
    for node in blockdevices:
        if not isinstance(node, dict):
            continue
        if node.get("type") != "disk":
            continue
        name = str(node.get("name") or "")
        if not name:
            continue
        tran = str(node.get("tran") or "").lower()
        model = str(node.get("model") or "").strip()
        serial = str(node.get("serial") or "").strip()
        is_nvme = name.startswith("nvme") or tran == "nvme"
        path = f"/dev/{name}"
        nvme_ctrl = _nvme_char_device(name) if is_nvme else None
        suggested = ["ioctl", "nvme-cli", "smartctl"] if is_nvme else ["smartctl"]
        if tran == "usb":
            suggested = ["smartctl", "usb-bridge-degraded"]

        size_bytes = _size_to_bytes(str(node.get("size") or ""))
        mount = node.get("mountpoint")
        letters: list[str] = []
        if mount:
            letters.append(str(mount))

        devices.append(
            InventoryDevice(
                device_path=path,
                friendly_name=model or name,
                model=model,
                serial=serial,
                size_bytes=size_bytes,
                media_type="ssd" if node.get("rota") == "0" else "hdd",
                bus_type=tran or ("nvme" if is_nvme else "unknown"),
                is_nvme=is_nvme,
                drive_letters=letters,
                linux_nvme_path=nvme_ctrl,
                suggested_telemetry=suggested,
            )
        )
    return devices


def _size_to_bytes(size_str: str) -> int | None:
    """Parse lsblk size like 1.8T, 512G into bytes (approximate)."""
    size_str = size_str.strip()
    if not size_str:
        return None
    mult = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    unit = size_str[-1].upper()
    if unit in mult:
        try:
            return int(float(size_str[:-1]) * mult[unit])
        except ValueError:
            return None
    try:
        return int(size_str)
    except ValueError:
        return None


def _fallback_sysfs() -> list[InventoryDevice]:
    """Minimal /sys/block scan when lsblk is unavailable."""
    devices: list[InventoryDevice] = []
    block = Path("/sys/block")
    if not block.is_dir():
        return devices
    for entry in sorted(block.iterdir()):
        name = entry.name
        if name.startswith("loop"):
            continue
        is_nvme = name.startswith("nvme")
        devices.append(
            InventoryDevice(
                device_path=f"/dev/{name}",
                friendly_name=name,
                model="",
                serial="",
                bus_type="nvme" if is_nvme else "unknown",
                is_nvme=is_nvme,
                linux_nvme_path=_nvme_char_device(name) if is_nvme else None,
                suggested_telemetry=["ioctl", "nvme-cli"] if is_nvme else ["smartctl"],
            )
        )
    return devices
