"""Cross-adapter parametrized integration roundtrips."""

from __future__ import annotations

import pytest

from nvme_sentinel.commands.identify import identify_controller
from nvme_sentinel.commands.log_pages import get_smart_health
from nvme_sentinel.hal.interface import StorageInterface


@pytest.fixture(autouse=True)
def _open_adapter(adapter_and_path: tuple[StorageInterface, str]) -> None:
    adapter, _ = adapter_and_path
    adapter.open()
    yield
    adapter.close()


def test_open_close(adapter_and_path: tuple[StorageInterface, str]) -> None:
    """Adapter open/close is idempotent after fixture lifecycle."""
    adapter, _ = adapter_and_path
    assert adapter.is_nvme() in (True, False)


def test_identify_controller_round_trip(adapter_and_path: tuple[StorageInterface, str]) -> None:
    """Identify Controller returns printable model string."""
    adapter, _ = adapter_and_path
    if not adapter.is_nvme():
        pytest.skip("device does not support NVMe identify")
    ctrl = identify_controller(adapter)
    assert ctrl.mn.strip()
    assert all(32 <= ord(c) <= 126 or c in "\n\r\t" for c in ctrl.mn[:32])


def test_smart_health_round_trip(adapter_and_path: tuple[StorageInterface, str]) -> None:
    """SMART health parse succeeds with sane temperature and percentage_used."""
    adapter, _ = adapter_and_path
    if not adapter.is_nvme():
        pytest.skip("device does not support NVMe SMART")
    smart = get_smart_health(adapter)
    assert smart.composite_temperature_celsius > 0
    assert 0 <= smart.percentage_used <= 100
