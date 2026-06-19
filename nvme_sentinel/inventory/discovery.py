"""Cross-platform device inventory entry point."""

from __future__ import annotations

import sys

from nvme_sentinel.inventory.linux import list_linux_devices
from nvme_sentinel.inventory.models import InventoryDevice
from nvme_sentinel.inventory.windows import list_windows_devices


def list_devices(*, timeout_s: float | None = None) -> list[InventoryDevice]:
    """Return storage devices for the current platform."""
    if sys.platform == "win32":
        if timeout_s is not None:
            return list_windows_devices(timeout_s=timeout_s)
        return list_windows_devices()
    if sys.platform == "linux":
        return list_linux_devices()
    return []
