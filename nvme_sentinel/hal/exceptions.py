"""Custom exception hierarchy for nvme-sentinel HAL and command flows."""

from __future__ import annotations


class NvmeSentinelError(Exception):
    """Base class for all nvme-sentinel exceptions."""


class DeviceError(NvmeSentinelError):
    """Device open/close or low-level I/O operation failed."""


class DeviceNotFound(DeviceError):  # noqa: N818
    """Requested storage device path was not found."""


class PermissionDenied(DeviceError):  # noqa: N818
    """Operation failed due to insufficient permissions."""


class AdminCommandError(NvmeSentinelError):
    """NVMe admin command completed with a non-zero status code."""

    def __init__(self, status_code: int, opcode: int, message: str) -> None:
        """Initialize an admin command error with status, opcode, and context."""
        self.status_code = status_code
        self.opcode = opcode
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        """Return an actionable string including opcode and status in hex."""
        return f"{self.message} (opcode=0x{self.opcode:02X}, status=0x{self.status_code:04X})"


class ParseError(NvmeSentinelError):
    """Byte-layout parsing failed for an NVMe payload."""


class CapabilityError(NvmeSentinelError):
    """Requested capability is not supported by the active adapter."""
