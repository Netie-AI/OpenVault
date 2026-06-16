from __future__ import annotations

from enum import Flag, IntEnum


class AdminOpcode(IntEnum):
    """NVMe admin command opcodes (implementation_plan.md §4.1)."""

    GET_LOG_PAGE = 0x02
    IDENTIFY = 0x06
    SET_FEATURES = 0x09
    GET_FEATURES = 0x0A
    FIRMWARE_COMMIT = 0x10
    FIRMWARE_DOWNLOAD = 0x11


class CNSValue(IntEnum):
    """Identify CNS values for opcode 0x06 (implementation_plan.md §4.2)."""

    IDENTIFY_NAMESPACE = 0x00
    IDENTIFY_CONTROLLER = 0x01
    ACTIVE_NAMESPACE_LIST = 0x02
    NAMESPACE_ID_DESCRIPTORS = 0x03


class LogPageID(IntEnum):
    """Get Log Page identifiers for opcode 0x02 (implementation_plan.md §4.3)."""

    ERROR_INFO = 0x01
    SMART_HEALTH = 0x02
    FIRMWARE_SLOT = 0x03
    CHANGED_NAMESPACE_LIST = 0x04
    COMMANDS_SUPPORTED = 0x05
    PERSISTENT_EVENT_LOG = 0x0D


class CriticalWarning(Flag):
    """SMART critical warning bits from byte 0 (implementation_plan.md §4.4)."""

    # preferred for Flag subclasses — bit position is self-documenting
    AVAILABLE_SPARE_LOW = 1 << 0  # bit 0
    TEMPERATURE_THRESHOLD = 1 << 1  # bit 1
    RELIABILITY_DEGRADED = 1 << 2  # bit 2
    READ_ONLY = 1 << 3  # bit 3
    VOLATILE_BACKUP_FAILED = 1 << 4  # bit 4
    PERSISTENT_MEMORY_READONLY = 1 << 5  # bit 5
