"""HostProxyAdapter replay tests."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from nvme_sentinel.adapters.host_proxy import HostProxyAdapter, is_host_proxy_path
from nvme_sentinel.commands.identify import identify_controller
from nvme_sentinel.commands.log_pages import get_smart_health
from nvme_sentinel.snapshot.schema import DeviceSnapshot
from nvme_sentinel.telemetry.source import TelemetrySource


@pytest.fixture
def snapshot_file(tmp_path: Path) -> Path:
    identify = (Path("tests/fixtures/identify_ctrl_generic.bin")).read_bytes()
    smart = (Path("tests/fixtures/smart_healthy.bin")).read_bytes()
    snap = DeviceSnapshot(
        device_path=r"\\.\PhysicalDrive0",
        platform="win32",
        telemetry_source=TelemetrySource.MOCK,
        identify_controller_b64=base64.standard_b64encode(identify).decode("ascii"),
        smart_health_b64=base64.standard_b64encode(smart).decode("ascii"),
        device_info={
            "path": r"\\.\PhysicalDrive0",
            "model": "Generic NVMe SSD Reference",
            "serial": "NVMESENTINEL0001",
            "firmware_rev": "GS01GR00",
            "namespace_count": 1,
            "is_nvme": True,
        },
    )
    path = tmp_path / "host_proxy.json"
    path.write_text(snap.model_dump_json(indent=2), encoding="utf-8")
    return path


def test_is_host_proxy_path() -> None:
    assert is_host_proxy_path("host-proxy:///tmp/snap.json")
    assert is_host_proxy_path("/reports/snap.json")
    assert not is_host_proxy_path(r"\\.\PhysicalDrive0")


def test_host_proxy_roundtrip(snapshot_file: Path) -> None:
    with HostProxyAdapter(snapshot_file) as dev:
        ctrl = identify_controller(dev)
        smart = get_smart_health(dev)
    assert "Generic NVMe SSD" in ctrl.mn
    assert smart.percentage_used == 3
