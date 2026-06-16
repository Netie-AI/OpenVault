"""Inventory discovery tests."""

from __future__ import annotations

import json
import sys

import pytest

from nvme_sentinel.inventory.discovery import list_devices
from nvme_sentinel.inventory.linux import list_linux_devices
from nvme_sentinel.inventory.windows import list_windows_devices


def test_list_linux_devices_parses_lsblk(monkeypatch: pytest.MonkeyPatch) -> None:
    lsblk_json = {
        "blockdevices": [
            {
                "name": "nvme0n1",
                "size": "1T",
                "type": "disk",
                "model": "TEST SSD",
                "serial": "SN123",
                "tran": "nvme",
                "rota": "0",
            }
        ]
    }

    class FakeResult:
        returncode = 0
        stdout = json.dumps(lsblk_json)

    monkeypatch.setattr(
        "nvme_sentinel.inventory.linux.subprocess.run",
        lambda *a, **k: FakeResult(),
    )
    devices = list_linux_devices()
    assert len(devices) == 1
    assert devices[0].is_nvme is True
    assert devices[0].linux_nvme_path == "/dev/nvme0"
    assert "ioctl" in devices[0].suggested_telemetry


def test_list_windows_devices_parses_powershell(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "DeviceId": 0,
            "FriendlyName": "Boot",
            "Model": "NVMe Drive",
            "SerialNumber": "ABC",
            "Size": 1000000000,
            "MediaType": "SSD",
            "BusType": "NVMe",
            "DriveLetters": "C:",
        }
    ]

    class FakeResult:
        returncode = 0
        stdout = json.dumps(payload)

    monkeypatch.setattr(
        "nvme_sentinel.inventory.windows.subprocess.run",
        lambda *a, **k: FakeResult(),
    )
    devices = list_windows_devices()
    assert len(devices) == 1
    assert devices[0].device_path == r"\\.\PhysicalDrive0"
    assert devices[0].is_nvme is True
    assert devices[0].drive_letters == ["C:"]


def test_list_devices_other_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert list_devices() == []
