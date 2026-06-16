"""Adapter factory: platform autodetect or forced backend."""

from __future__ import annotations

import sys
from typing import Literal

from nvme_sentinel.hal.interface import StorageInterface

AdapterForce = Literal["linux", "windows", "mock", "host-proxy"]


def get_adapter(
    device_path: str | None = None,
    force: AdapterForce | None = None,
) -> StorageInterface:
    """
    Auto-detect or force an adapter.

    force=None + device_path=None  → MockNvmeAdapter

    force=None + device_path given → platform-detect (linux/win32) or host-proxy JSON

    force="mock"                   → MockNvmeAdapter always

    force="linux"                  → LinuxNvmeAdapter(device_path) — device_path required

    force="windows"                → WindowsStorageAdapter(device_path) — device_path required

    force="host-proxy"             → HostProxyAdapter(snapshot JSON path)
    """
    from nvme_sentinel.adapters.host_proxy import (
        HostProxyAdapter,
        is_host_proxy_path,
        resolve_host_proxy_path,
    )
    from nvme_sentinel.adapters.mock import MockNvmeAdapter

    if force == "host-proxy" or (force is None and is_host_proxy_path(device_path)):
        if not device_path:
            raise ValueError("device_path required for host-proxy adapter")
        return HostProxyAdapter(resolve_host_proxy_path(device_path))

    if force == "mock" or (force is None and device_path is None):
        return MockNvmeAdapter(device_path or "/dev/mock-nvme0")

    if force == "linux":
        if not device_path:
            raise ValueError("device_path required for linux adapter")
        from nvme_sentinel.adapters.linux import LinuxNvmeAdapter

        return LinuxNvmeAdapter(device_path)

    if force == "windows":
        if not device_path:
            raise ValueError("device_path required for windows adapter")
        from nvme_sentinel.adapters.windows import WindowsStorageAdapter

        return WindowsStorageAdapter(device_path)

    # Auto-detect
    if sys.platform == "linux":
        from nvme_sentinel.adapters.linux import LinuxNvmeAdapter

        return LinuxNvmeAdapter(device_path or "/dev/nvme0n1")
    if sys.platform == "win32":
        from nvme_sentinel.adapters.windows import WindowsStorageAdapter

        return WindowsStorageAdapter(device_path or r"\\.\PhysicalDrive0")
    return MockNvmeAdapter(device_path or "/dev/mock-nvme0")
