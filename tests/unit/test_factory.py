"""Tests for hal.factory.get_adapter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nvme_sentinel.adapters.linux import LinuxNvmeAdapter
from nvme_sentinel.adapters.mock import MockNvmeAdapter
from nvme_sentinel.adapters.windows import WindowsStorageAdapter
from nvme_sentinel.hal.factory import get_adapter


def test_force_mock() -> None:
    adapter = get_adapter(force="mock")
    assert isinstance(adapter, MockNvmeAdapter)


def test_force_linux() -> None:
    adapter = get_adapter(force="linux", device_path="/dev/nvme0")
    assert isinstance(adapter, LinuxNvmeAdapter)
    assert adapter.device_path == "/dev/nvme0"


def test_force_windows() -> None:
    adapter = get_adapter(force="windows", device_path=r"\\.\PhysicalDrive0")
    assert isinstance(adapter, WindowsStorageAdapter)
    assert adapter.device_path == r"\\.\PhysicalDrive0"


def test_autodetect_no_path() -> None:
    adapter = get_adapter()
    assert isinstance(adapter, MockNvmeAdapter)


def test_autodetect_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    adapter = get_adapter("/dev/nvme0")
    assert isinstance(adapter, LinuxNvmeAdapter)


def test_autodetect_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    adapter = get_adapter(r"\\.\PhysicalDrive0")
    assert isinstance(adapter, WindowsStorageAdapter)


def test_autodetect_other_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    adapter = get_adapter("/dev/nvme0")
    assert isinstance(adapter, MockNvmeAdapter)


def test_force_linux_no_path_raises() -> None:
    with pytest.raises(ValueError, match="device_path required"):
        get_adapter(force="linux")


def test_force_host_proxy(tmp_path: Path) -> None:
    from nvme_sentinel.adapters.host_proxy import HostProxyAdapter

    snap = tmp_path / "empty.json"
    snap.write_text(
        '{"schema_version":"1","device_path":"/x","platform":"linux",'
        '"telemetry_source":"mock","readonly":true}',
        encoding="utf-8",
    )
    adapter = get_adapter(str(snap), force="host-proxy")
    assert isinstance(adapter, HostProxyAdapter)
