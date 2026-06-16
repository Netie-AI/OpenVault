"""Linux NVMe passthrough native structs and constants.

`NVME_IOCTL_ADMIN_CMD` is computed for the generic `<asm-generic/ioctl.h>` layout used
by x86_64, aarch64, arm, riscv64, and s390x. PowerPC, SPARC, MIPS, PA-RISC, and Alpha
use different `_IOC_*SHIFT` values and would compute a different constant; that is out
of scope for this project.
"""

from __future__ import annotations

import ctypes

from nvme_sentinel.hal.interface import AdminCommand


class NvmePassthruCmd(ctypes.Structure):
    """ctypes mirror of `struct nvme_passthru_cmd` from `<linux/nvme_ioctl.h>`."""

    _pack_ = 1
    _fields_ = [
        ("opcode", ctypes.c_uint8),
        ("flags", ctypes.c_uint8),
        ("rsvd1", ctypes.c_uint16),
        ("nsid", ctypes.c_uint32),
        ("cdw2", ctypes.c_uint32),
        ("cdw3", ctypes.c_uint32),
        ("metadata", ctypes.c_uint64),
        ("addr", ctypes.c_uint64),
        ("metadata_len", ctypes.c_uint32),
        ("data_len", ctypes.c_uint32),
        ("cdw10", ctypes.c_uint32),
        ("cdw11", ctypes.c_uint32),
        ("cdw12", ctypes.c_uint32),
        ("cdw13", ctypes.c_uint32),
        ("cdw14", ctypes.c_uint32),
        ("cdw15", ctypes.c_uint32),
        ("timeout_ms", ctypes.c_uint32),
        ("result", ctypes.c_uint32),
    ]


# _IOWR('N', 0x41, sizeof(nvme_passthru_cmd)) using asm-generic ioctl layout:
# (3 << 30) | (72 << 16) | (ord('N') << 8) | 0x41 = 0xC0484E41
NVME_IOCTL_ADMIN_CMD = 0xC0484E41


assert ctypes.sizeof(NvmePassthruCmd) == 72, (
    f"NvmePassthruCmd size {ctypes.sizeof(NvmePassthruCmd)} != 72"
)


def build_admin_cmd(cmd: AdminCommand, data_buf: ctypes.Array[ctypes.c_ubyte]) -> NvmePassthruCmd:
    """Build native passthrough struct from an AdminCommand and data buffer."""
    native = NvmePassthruCmd()
    native.opcode = cmd.opcode
    native.flags = 0
    native.rsvd1 = 0
    native.nsid = cmd.nsid
    native.cdw2 = 0
    native.cdw3 = 0
    # metadata is unused by current project scope; set explicitly to zero.
    native.metadata = 0
    native.addr = ctypes.addressof(data_buf)
    native.metadata_len = 0
    native.data_len = cmd.data_len
    native.cdw10 = cmd.cdw10
    native.cdw11 = cmd.cdw11
    native.cdw12 = cmd.cdw12
    native.cdw13 = cmd.cdw13
    native.cdw14 = cmd.cdw14
    native.cdw15 = cmd.cdw15
    native.timeout_ms = cmd.timeout_ms
    # result is output-only and populated by kernel on ioctl return.
    return native
