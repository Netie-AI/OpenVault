"""Unit tests for Identify command builders."""

from __future__ import annotations

from pathlib import Path

import pytest

from nvme_sentinel.adapters.mock import MockNvmeAdapter
from nvme_sentinel.commands.identify import (
    active_namespace_list,
    identify_controller,
    identify_namespace,
)
from nvme_sentinel.hal.exceptions import AdminCommandError
from nvme_sentinel.hal.interface import AdminCommand, CommandResult


def _mock_adapter() -> MockNvmeAdapter:
    fixtures = Path("tests/fixtures")
    adapter = MockNvmeAdapter(
        identify_ctrl_path=fixtures / "identify_ctrl_generic.bin",
        identify_ns_path=fixtures / "identify_ns.bin",
        smart_path=fixtures / "smart_healthy.bin",
    )
    adapter.open()
    return adapter


def test_identify_controller_returns_expected_vid() -> None:
    """Identify Controller parser returns fixture VID."""
    adapter = _mock_adapter()
    result = identify_controller(adapter)
    assert result.vid == 0x144D


def test_identify_namespace_returns_expected_nsze() -> None:
    """Identify Namespace parser returns fixture NSZE value."""
    adapter = _mock_adapter()
    result = identify_namespace(adapter, nsid=1)
    assert result.nsze == 0x200000


def test_identify_namespace_zero_nsid_raises_value_error() -> None:
    """Command layer validates nsid > 0 before parser call."""
    adapter = _mock_adapter()
    with pytest.raises(ValueError):
        identify_namespace(adapter, nsid=0)


def test_active_namespace_list_returns_single_nsid() -> None:
    """Active namespace list parsing stops at first zero entry."""
    adapter = _mock_adapter()
    assert active_namespace_list(adapter) == [1]


def test_identify_controller_admin_error_propagates() -> None:
    """AdminCommandError from adapter is not swallowed."""
    adapter = _mock_adapter()

    def _raise_admin_error(_: AdminCommand) -> CommandResult:
        raise AdminCommandError(status_code=0x01, opcode=0x06, message="forced failure")

    adapter.admin_passthru = _raise_admin_error  # type: ignore[assignment]  # test monkeypatch on bound method

    with pytest.raises(AdminCommandError):
        identify_controller(adapter)
