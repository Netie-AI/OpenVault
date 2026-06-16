"""Unit tests for WMI fallback helpers (no PowerShell execution)."""

from __future__ import annotations

import pytest

from nvme_sentinel.adapters._wmi_fallback import disk_number_from_path


def test_disk_number_from_path_physical_drive() -> None:
    assert disk_number_from_path(r"\\.\PhysicalDrive2") == 2


def test_disk_number_from_path_invalid() -> None:
    with pytest.raises(ValueError, match="Cannot parse"):
        disk_number_from_path("not-a-disk-path")
