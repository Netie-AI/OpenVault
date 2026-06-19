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


def test_list_windows_devices_passes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, float] = {}

    class FakeResult:
        returncode = 0
        stdout = "[]"

    def fake_run(*args: object, **kwargs: object) -> FakeResult:
        timeout = kwargs.get("timeout")
        if isinstance(timeout, (int, float)):
            seen["timeout"] = float(timeout)
        return FakeResult()

    monkeypatch.setattr("nvme_sentinel.inventory.windows.subprocess.run", fake_run)
    list_windows_devices(timeout_s=4.5)
    assert seen["timeout"] == 4.5


def test_list_windows_devices_timeout_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def fake_run(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr("nvme_sentinel.inventory.windows.subprocess.run", fake_run)
    assert list_windows_devices(timeout_s=1.0) == []


def test_list_devices_other_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert list_devices() == []
