"""Get Log Page command builders and parsers."""

from __future__ import annotations

import structlog

from nvme_sentinel.hal.enums import AdminOpcode, LogPageID
from nvme_sentinel.hal.exceptions import AdminCommandError
from nvme_sentinel.hal.interface import AdminCommand, StorageInterface
from nvme_sentinel.models.error_log import ErrorLogEntry
from nvme_sentinel.models.firmware import FirmwareSlotInfo
from nvme_sentinel.models.smart import SmartHealthLog

_LOG = structlog.get_logger()


def _numdl_bits(size_bytes: int) -> int:
    if size_bytes <= 0 or size_bytes % 4 != 0:
        raise ValueError(f"log page size must be positive multiple of 4, got {size_bytes}")
    numdl = (size_bytes // 4) - 1
    return numdl << 16


def get_smart_health(device: StorageInterface, nsid: int = 0xFFFFFFFF) -> SmartHealthLog:
    """Fetch SMART / Health log (LID 0x02) and parse typed metrics."""
    if nsid != 0xFFFFFFFF:
        _LOG.warning("namespace_scoped_smart_requested", nsid=nsid)

    # implementation_plan.md §4.3, §4.4: SMART log is 512 bytes with LID in cdw10[7:0].
    cdw10 = int(LogPageID.SMART_HEALTH) | _numdl_bits(512)
    cmd = AdminCommand(
        opcode=int(AdminOpcode.GET_LOG_PAGE),
        nsid=nsid,
        cdw10=cdw10,
        data_len=512,
    )
    result = device.admin_passthru(cmd)
    if result.status != 0:
        raise AdminCommandError(
            status_code=result.status,
            opcode=int(AdminOpcode.GET_LOG_PAGE),
            message=f"Get SMART Health failed for nsid={nsid}",
        )
    return SmartHealthLog.from_bytes(result.data)


def get_error_info_log(device: StorageInterface, n_entries: int) -> list[ErrorLogEntry]:
    """Fetch Error Information log entries (64 bytes each) and parse them."""
    if n_entries <= 0:
        raise ValueError("n_entries must be > 0")

    size_bytes = n_entries * 64
    cdw10 = int(LogPageID.ERROR_INFO) | _numdl_bits(size_bytes)
    cmd = AdminCommand(
        opcode=int(AdminOpcode.GET_LOG_PAGE),
        cdw10=cdw10,
        data_len=size_bytes,
    )
    result = device.admin_passthru(cmd)
    if result.status != 0:
        raise AdminCommandError(
            status_code=result.status,
            opcode=int(AdminOpcode.GET_LOG_PAGE),
            message=f"Get Error Information log failed for entries={n_entries}",
        )

    entries: list[ErrorLogEntry] = []
    for offset in range(0, len(result.data), 64):
        chunk = result.data[offset : offset + 64]
        if len(chunk) < 64:
            break
        entries.append(ErrorLogEntry.from_bytes(chunk))
    return entries


def get_firmware_slot_info(device: StorageInterface) -> FirmwareSlotInfo:
    """Fetch Firmware Slot Information log (LID 0x03)."""
    # implementation_plan.md §4.3: Firmware Slot log is 512 bytes.
    cdw10 = int(LogPageID.FIRMWARE_SLOT) | _numdl_bits(512)
    cmd = AdminCommand(
        opcode=int(AdminOpcode.GET_LOG_PAGE),
        cdw10=cdw10,
        data_len=512,
    )
    result = device.admin_passthru(cmd)
    if result.status != 0:
        raise AdminCommandError(
            status_code=result.status,
            opcode=int(AdminOpcode.GET_LOG_PAGE),
            message="Get Firmware Slot Information failed",
        )
    return FirmwareSlotInfo.from_bytes(result.data)
