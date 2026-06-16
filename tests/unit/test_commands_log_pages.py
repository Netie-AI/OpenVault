"""Unit tests for Get Log Page command builders."""

from __future__ import annotations

from pathlib import Path

import pytest

from nvme_sentinel.adapters.mock import MockNvmeAdapter
from nvme_sentinel.commands.log_pages import (
    get_error_info_log,
    get_firmware_slot_info,
    get_smart_health,
)


def _mock_adapter() -> MockNvmeAdapter:
    fixtures = Path("tests/fixtures")
    adapter = MockNvmeAdapter(
        identify_ctrl_path=fixtures / "identify_ctrl_generic.bin",
        identify_ns_path=fixtures / "identify_ns.bin",
        smart_path=fixtures / "smart_healthy.bin",
    )
    adapter.open()
    return adapter


def test_get_smart_health_returns_expected_percentage_used() -> None:
    """SMART command parses expected percentage used from fixture payload."""
    adapter = _mock_adapter()
    log = get_smart_health(adapter)
    assert log.percentage_used == 3


def test_get_error_info_log_returns_zeroed_entries() -> None:
    """Error log command returns requested number of zeroed entries from mock."""
    adapter = _mock_adapter()
    entries = get_error_info_log(adapter, n_entries=2)
    assert len(entries) == 2
    assert entries[0].error_count == 0
    assert entries[0].status_field == 0


def test_get_error_info_log_validates_entry_count() -> None:
    """Error log command enforces n_entries > 0."""
    adapter = _mock_adapter()
    with pytest.raises(ValueError):
        get_error_info_log(adapter, n_entries=0)


def test_get_firmware_slot_info_returns_expected_primary_slot() -> None:
    """Firmware slot command parses AFI and FRS1 from mock payload."""
    adapter = _mock_adapter()
    info = get_firmware_slot_info(adapter)
    assert info.afi == 0x01
    assert info.frs[0] == "GS01GR00"
