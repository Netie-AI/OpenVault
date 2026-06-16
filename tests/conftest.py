"""Shared pytest fixtures: parametrized adapters and hardware gating."""

from __future__ import annotations

import os
import sys

import pytest

from nvme_sentinel.hal.factory import get_adapter
from nvme_sentinel.hal.interface import StorageInterface


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip hardware- and platform-gated tests unless the environment matches."""
    if not os.environ.get("NVME_SENTINEL_REAL_DEVICE"):
        skip_nvme = pytest.mark.skip(
            reason="requires real NVMe device (set NVME_SENTINEL_REAL_DEVICE=1)"
        )
        for item in items:
            if "requires_nvme" in item.keywords:
                item.add_marker(skip_nvme)

    if sys.platform != "win32":
        skip_windows = pytest.mark.skip(reason="windows_only")
        for item in items:
            if "windows_only" in item.keywords:
                item.add_marker(skip_windows)

    if sys.platform != "linux":
        skip_linux = pytest.mark.skip(reason="linux_only")
        for item in items:
            if "linux_only" in item.keywords:
                item.add_marker(skip_linux)


@pytest.fixture(
    params=[
        pytest.param(("mock", "/dev/mock-nvme0"), id="mock"),
        pytest.param(
            ("linux", "/dev/nvme0n1"),
            marks=[pytest.mark.requires_nvme, pytest.mark.linux_only],
            id="linux",
        ),
        pytest.param(
            ("windows", r"\\.\PhysicalDrive0"),
            marks=[pytest.mark.requires_nvme, pytest.mark.windows_only],
            id="windows",
        ),
    ],
)
def adapter_and_path(request: pytest.FixtureRequest) -> tuple[StorageInterface, str]:
    """Parametrized adapter for cross-platform roundtrip tests."""
    kind, path = request.param
    adapter = get_adapter(device_path=path, force=kind)
    return adapter, path
