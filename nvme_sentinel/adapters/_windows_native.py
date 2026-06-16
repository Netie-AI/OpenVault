"""Windows STORAGE_PROTOCOL_COMMAND layout from ntddstor.h (implementation_plan.md §4.6)."""

from __future__ import annotations

import ctypes

from nvme_sentinel.hal.interface import AdminCommand

# IOCTL_STORAGE_PROTOCOL_COMMAND — defined in ntddstor.h (do not re-derive from CTL_CODE;
# MSDN/SDK value is authoritative).
IOCTL_STORAGE_PROTOCOL_COMMAND = 0x002DD3C8
# CTL_CODE(IOCTL_STORAGE_BASE=0x2D, 0x04F2, METHOD_BUFFERED=0,
#          FILE_READ_WRITE_ACCESS=3)
# = (0x2D<<16)|(3<<14)|(0x04F2<<2)|0
# = 0x002D0000|0x0000C000|0x000013C8 = 0x002DD3C8
# Verified by PowerShell CTL_CODE computation 2026-05-10

STORAGE_PROTOCOL_TYPE_NVME = 3
STORAGE_PROTOCOL_COMMAND_FLAG_ADAPTER_REQUEST = 0x80000000
STORAGE_PROTOCOL_STRUCTURE_VERSION = 1
STORAGE_PROTOCOL_SPECIFIC_NVME_ADMIN_COMMAND = 0x1
STORAGE_PROTOCOL_COMMAND_LENGTH_NVME = 64
NVME_ERROR_INFO_SIZE = 64  # sizeof(NVME_ERROR_INFO_LOG) from nvme.h

CMD_OFFSET = 80  # NVMe SQE (64 bytes) after header
ERR_OFFSET = 144  # ErrorInfo buffer (64 bytes)
DATA_OFFSET = 208  # Host data buffer


class StorageProtocolCommandHeader(ctypes.Structure):
    """Fixed 80-byte header prefix of STORAGE_PROTOCOL_COMMAND (fields per ntddstor.h)."""

    _pack_ = 1
    _fields_ = [
        ("Version", ctypes.c_uint32),
        ("Length", ctypes.c_uint32),
        ("ProtocolType", ctypes.c_uint32),  # ProtocolTypeNvme = 3
        ("Flags", ctypes.c_uint32),
        ("ReturnStatus", ctypes.c_uint32),
        ("ErrorCode", ctypes.c_uint32),
        ("CommandLength", ctypes.c_uint32),
        ("ErrorInfoLength", ctypes.c_uint32),
        ("DataToDeviceTransferLength", ctypes.c_uint32),
        ("DataFromDeviceTransferLength", ctypes.c_uint32),
        ("TimeOutValue", ctypes.c_uint32),
        ("ErrorInfoOffset", ctypes.c_uint32),
        ("DataToDeviceBufferOffset", ctypes.c_uint32),
        ("DataFromDeviceBufferOffset", ctypes.c_uint32),
        ("CommandSpecific", ctypes.c_uint32),
        ("Reserved0", ctypes.c_uint32),
        ("FixedProtocolReturnData", ctypes.c_uint32),
        ("Reserved1", ctypes.c_uint32 * 3),
    ]


assert ctypes.sizeof(StorageProtocolCommandHeader) == 80


def build_protocol_command(cmd: AdminCommand) -> bytes:
    """Build IOCTL_STORAGE_PROTOCOL_COMMAND buffer: header, SQE, error-info, data (ntddstor.h)."""
    total = DATA_OFFSET + max(cmd.data_len, 0)
    buf = bytearray(total)
    hdr = StorageProtocolCommandHeader.from_buffer(buf)
    hdr.Version = STORAGE_PROTOCOL_STRUCTURE_VERSION
    hdr.Length = 80
    hdr.ProtocolType = STORAGE_PROTOCOL_TYPE_NVME
    hdr.Flags = STORAGE_PROTOCOL_COMMAND_FLAG_ADAPTER_REQUEST
    hdr.CommandLength = STORAGE_PROTOCOL_COMMAND_LENGTH_NVME
    hdr.CommandSpecific = STORAGE_PROTOCOL_SPECIFIC_NVME_ADMIN_COMMAND
    hdr.ErrorInfoLength = NVME_ERROR_INFO_SIZE
    hdr.ErrorInfoOffset = ERR_OFFSET
    hdr.DataToDeviceTransferLength = 0
    hdr.DataFromDeviceTransferLength = max(cmd.data_len, 0)
    hdr.DataFromDeviceBufferOffset = DATA_OFFSET if cmd.data_len > 0 else 0
    hdr.TimeOutValue = max(cmd.timeout_ms // 1000, 10)
    sqe = memoryview(buf)[CMD_OFFSET : CMD_OFFSET + 64]
    sqe[0] = cmd.opcode & 0xFF
    sqe[4:8] = cmd.nsid.to_bytes(4, "little")
    sqe[40:44] = cmd.cdw10.to_bytes(4, "little")
    sqe[44:48] = cmd.cdw11.to_bytes(4, "little")
    sqe[48:52] = cmd.cdw12.to_bytes(4, "little")
    sqe[52:56] = cmd.cdw13.to_bytes(4, "little")
    sqe[56:60] = cmd.cdw14.to_bytes(4, "little")
    sqe[60:64] = cmd.cdw15.to_bytes(4, "little")
    return bytes(buf)
