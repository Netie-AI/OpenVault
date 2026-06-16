"""Unit tests for deterministic mock NVMe adapter behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from nvme_sentinel.adapters.mock import MockNvmeAdapter
from nvme_sentinel.hal.enums import AdminOpcode, CNSValue, LogPageID
from nvme_sentinel.hal.exceptions import AdminCommandError, CapabilityError, DeviceError
from nvme_sentinel.hal.interface import AdminCommand
from nvme_sentinel.models.smart import SmartHealthLog


def _fixtures() -> tuple[Path, Path, Path]:
    base = Path("tests/fixtures")
    return (
        base / "identify_ctrl_generic.bin",
        base / "identify_ns.bin",
        base / "smart_healthy.bin",
    )


def test_open_close_state_machine() -> None:
    """admin_passthru fails when closed and succeeds when opened."""
    ctrl, ns, smart = _fixtures()
    adapter = MockNvmeAdapter(
        identify_ctrl_path=ctrl,
        identify_ns_path=ns,
        smart_path=smart,
    )
    with pytest.raises(DeviceError):
        adapter.admin_passthru(AdminCommand(opcode=int(AdminOpcode.IDENTIFY), data_len=4096))
    adapter.open()
    result = adapter.admin_passthru(
        AdminCommand(
            opcode=int(AdminOpcode.IDENTIFY),
            cdw10=int(CNSValue.IDENTIFY_CONTROLLER),
            data_len=4096,
        )
    )
    assert len(result.data) == 4096
    adapter.close()


def test_identify_controller_payload_and_vid() -> None:
    """Identify Controller returns 4096 bytes with expected little-endian VID."""
    ctrl, ns, smart = _fixtures()
    adapter = MockNvmeAdapter(identify_ctrl_path=ctrl, identify_ns_path=ns, smart_path=smart)
    adapter.open()
    result = adapter.admin_passthru(
        AdminCommand(
            opcode=int(AdminOpcode.IDENTIFY),
            cdw10=int(CNSValue.IDENTIFY_CONTROLLER),
            data_len=4096,
        )
    )
    assert len(result.data) == 4096
    assert int.from_bytes(result.data[0:2], "little") == 0x144D


def test_identify_namespace_payload() -> None:
    """Identify Namespace returns expected fixture length."""
    ctrl, ns, smart = _fixtures()
    adapter = MockNvmeAdapter(identify_ctrl_path=ctrl, identify_ns_path=ns, smart_path=smart)
    adapter.open()
    result = adapter.admin_passthru(
        AdminCommand(
            opcode=int(AdminOpcode.IDENTIFY),
            cdw10=int(CNSValue.IDENTIFY_NAMESPACE),
            nsid=1,
            data_len=4096,
        )
    )
    assert len(result.data) == 4096


def test_smart_log_page_dispatch_uses_lid_mask() -> None:
    """Get Log Page extracts LID from cdw10 low byte."""
    ctrl, ns, smart = _fixtures()
    adapter = MockNvmeAdapter(identify_ctrl_path=ctrl, identify_ns_path=ns, smart_path=smart)
    adapter.open()
    cdw10 = int(LogPageID.SMART_HEALTH) | (127 << 16)
    result = adapter.admin_passthru(
        AdminCommand(
            opcode=int(AdminOpcode.GET_LOG_PAGE),
            cdw10=cdw10,
            data_len=512,
        )
    )
    log = SmartHealthLog.from_bytes(result.data)
    assert log.percentage_used == 3


def test_unknown_opcode_raises_capability_error() -> None:
    """Unsupported opcode returns CapabilityError."""
    ctrl, ns, smart = _fixtures()
    adapter = MockNvmeAdapter(identify_ctrl_path=ctrl, identify_ns_path=ns, smart_path=smart)
    adapter.open()
    with pytest.raises(CapabilityError):
        adapter.admin_passthru(AdminCommand(opcode=0xFF))


def test_determinism_two_instances_same_bytes() -> None:
    """Two adapters with same fixtures return byte-identical command results."""
    ctrl, ns, smart = _fixtures()
    left = MockNvmeAdapter(identify_ctrl_path=ctrl, identify_ns_path=ns, smart_path=smart)
    right = MockNvmeAdapter(identify_ctrl_path=ctrl, identify_ns_path=ns, smart_path=smart)
    left.open()
    right.open()
    cmd = AdminCommand(
        opcode=int(AdminOpcode.IDENTIFY),
        cdw10=int(CNSValue.IDENTIFY_CONTROLLER),
        data_len=4096,
    )
    assert left.admin_passthru(cmd).data == right.admin_passthru(cmd).data


def test_context_manager_closes_on_exit() -> None:
    """Context manager opens and closes adapter state."""
    ctrl, ns, smart = _fixtures()
    adapter = MockNvmeAdapter(identify_ctrl_path=ctrl, identify_ns_path=ns, smart_path=smart)
    with adapter as ctx:
        result = ctx.admin_passthru(
            AdminCommand(
                opcode=int(AdminOpcode.IDENTIFY),
                cdw10=int(CNSValue.ACTIVE_NAMESPACE_LIST),
                data_len=4096,
            )
        )
        assert result.data[:4] == (1).to_bytes(4, "little")
    with pytest.raises(DeviceError):
        adapter.admin_passthru(
            AdminCommand(
                opcode=int(AdminOpcode.IDENTIFY),
                cdw10=int(CNSValue.IDENTIFY_CONTROLLER),
                data_len=4096,
            )
        )


def test_unknown_identify_cns_raises_admin_command_error() -> None:
    """Unknown Identify CNS raises AdminCommandError."""
    ctrl, ns, smart = _fixtures()
    adapter = MockNvmeAdapter(identify_ctrl_path=ctrl, identify_ns_path=ns, smart_path=smart)
    adapter.open()
    with pytest.raises(AdminCommandError):
        adapter.admin_passthru(
            AdminCommand(opcode=int(AdminOpcode.IDENTIFY), cdw10=0xEE, data_len=4096)
        )
