"""Windows NVMe admin passthrough via DeviceIoControl(IOCTL_STORAGE_PROTOCOL_COMMAND)."""

from __future__ import annotations

import ctypes
import re
import subprocess
import sys
from typing import Protocol

from nvme_sentinel.adapters._windows_native import (
    DATA_OFFSET,
    IOCTL_STORAGE_PROTOCOL_COMMAND,
    StorageProtocolCommandHeader,
    build_protocol_command,
)
from nvme_sentinel.commands.identify import active_namespace_list, identify_controller
from nvme_sentinel.hal.base import BaseAdapter
from nvme_sentinel.hal.enums import AdminOpcode, CNSValue
from nvme_sentinel.hal.exceptions import (
    AdminCommandError,
    CapabilityError,
    DeviceError,
    DeviceNotFound,
    PermissionDenied,
)
from nvme_sentinel.hal.interface import AdminCommand, CommandResult, DeviceInfo

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5

_PERMISSION_HINT = "run PowerShell as Administrator or use: Start-Process powershell -Verb RunAs"


def _volume_path_for_physical(physical_path: str) -> str | None:
    """
    Map \\\\.\\PhysicalDriveN to a volume path (\\\\.\\C:) using WMI via PowerShell.
    Returns None if no drive letter is found.
    """
    m = re.search(r"PhysicalDrive(\d+)", physical_path, re.IGNORECASE)
    if not m:
        return None
    idx = int(m.group(1))
    result = subprocess.run(
        [
            "powershell",
            "-Command",
            f"Get-Partition -DiskNumber {idx} | Get-Volume | "
            "Select-Object -ExpandProperty DriveLetter",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    for line in result.stdout.splitlines():
        letter = line.strip()
        if len(letter) == 1 and letter.isalpha():
            return f"\\\\.\\{letter}:"
    return None


class _Win32Api(Protocol):
    """Seam for kernel32 DeviceIoControl / file APIs (replace in unit tests)."""

    def create_file(self, path: str, access: int, share: int, disp: int) -> int:
        """Return OS handle as int, or INVALID_HANDLE_VALUE on failure."""
        ...

    def device_io_control(
        self,
        handle: int,
        code: int,
        in_buf: bytes | bytearray,
        out_buf: bytearray,
    ) -> tuple[bool, int]:
        """Return (success, bytes_returned)."""
        ...

    def close_handle(self, handle: int) -> None:
        """Close an open object handle."""
        ...

    def get_last_error(self) -> int:
        """Return GetLastError() code."""
        ...


class _Win32ApiStub:
    """No-op / failure stub when not on Windows (imports must not load kernel32)."""

    def create_file(self, path: str, access: int, share: int, disp: int) -> int:
        return -1

    def device_io_control(
        self,
        handle: int,
        code: int,
        in_buf: bytes | bytearray,
        out_buf: bytearray,
    ) -> tuple[bool, int]:
        return (False, 0)

    def close_handle(self, handle: int) -> None:
        return None

    def get_last_error(self) -> int:
        return 0


class _Win32ApiImpl:
    """Real kernel32 bindings for CreateFileW / DeviceIoControl / CloseHandle."""

    def __init__(self) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self._kernel32.CreateFileW.restype = ctypes.c_void_p
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_bool
        self._kernel32.GetLastError.restype = ctypes.c_uint32
        self._kernel32.DeviceIoControl.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        self._kernel32.DeviceIoControl.restype = ctypes.c_bool

    def create_file(self, path: str, access: int, share: int, disp: int) -> int:
        handle = int(
            self._kernel32.CreateFileW(
                path,
                access,
                share,
                None,
                disp,
                FILE_ATTRIBUTE_NORMAL,
                None,
            )
        )
        return handle

    def device_io_control(
        self,
        handle: int,
        code: int,
        in_buf: bytes | bytearray,
        out_buf: bytearray,
    ) -> tuple[bool, int]:
        in_len = len(in_buf)
        out_len = len(out_buf)
        in_block = (ctypes.c_ubyte * in_len).from_buffer_copy(in_buf)
        out_block = (ctypes.c_ubyte * out_len).from_buffer(out_buf)
        bytes_returned = ctypes.c_uint32(0)
        ok = bool(
            self._kernel32.DeviceIoControl(
                ctypes.c_void_p(handle),
                code,
                ctypes.cast(in_block, ctypes.c_void_p),
                in_len,
                ctypes.cast(out_block, ctypes.c_void_p),
                out_len,
                ctypes.byref(bytes_returned),
                None,
            )
        )
        return ok, int(bytes_returned.value)

    def close_handle(self, handle: int) -> None:
        self._kernel32.CloseHandle(ctypes.c_void_p(handle))

    def get_last_error(self) -> int:
        return int(self._kernel32.GetLastError())


_win32: _Win32Api = _Win32ApiImpl() if sys.platform == "win32" else _Win32ApiStub()


def _handle_invalid(handle: int) -> bool:
    """True if CreateFileW returned INVALID_HANDLE_VALUE (32- or 64-bit pointer -1)."""
    return handle in (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF)


class WindowsStorageAdapter(BaseAdapter):
    """
    NVMe admin passthrough via DeviceIoControl(IOCTL_STORAGE_PROTOCOL_COMMAND).

    Requires Windows + admin rights. On non-Windows, open() raises CapabilityError.

    Mock seam: module-level _win32 object wraps all kernel32 calls so unit tests
    replace it without touching sys.platform.
    """

    def __init__(self, device_path: str) -> None:
        super().__init__()
        self.device_path = device_path
        self._handle: int | None = None

    def open(self) -> None:
        if sys.platform != "win32":
            raise CapabilityError("WindowsStorageAdapter requires Windows")
        if self._handle is not None:
            return
        path = self.device_path
        handle = _win32.create_file(
            path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            OPEN_EXISTING,
        )
        if _handle_invalid(handle):
            err = _win32.get_last_error()
            if err == ERROR_FILE_NOT_FOUND:
                raise DeviceNotFound(f"device not found: {path}")
            if err == ERROR_ACCESS_DENIED:
                alt = _volume_path_for_physical(path)
                if alt is not None:
                    handle = _win32.create_file(
                        alt,
                        GENERIC_READ | GENERIC_WRITE,
                        FILE_SHARE_READ | FILE_SHARE_WRITE,
                        OPEN_EXISTING,
                    )
                    if not _handle_invalid(handle):
                        self.device_path = alt
                        self._handle = handle
                        return
                raise PermissionDenied(f"permission denied opening {path} -- {_PERMISSION_HINT}")
            raise DeviceError(f"CreateFileW failed for {path}: error={err}")
        self._handle = handle

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            _win32.close_handle(self._handle)
        finally:
            self._handle = None

    def admin_passthru(self, cmd: AdminCommand) -> CommandResult:
        if self._handle is None:
            raise DeviceError("device not opened")
        handle_io = self._handle
        with self._timed(cmd) as record:
            in_buf = build_protocol_command(cmd)
            out_buf = bytearray(len(in_buf))
            ok, _ = _win32.device_io_control(
                handle_io,
                IOCTL_STORAGE_PROTOCOL_COMMAND,
                in_buf,
                out_buf,
            )
            if not ok:
                raise AdminCommandError(
                    status_code=_win32.get_last_error(),
                    opcode=cmd.opcode,
                    message="DeviceIoControl failed",
                )
            hdr = StorageProtocolCommandHeader.from_buffer_copy(bytes(out_buf[:80]))
            if hdr.ReturnStatus != 0:
                raise AdminCommandError(
                    status_code=int(hdr.ReturnStatus),
                    opcode=cmd.opcode,
                    message=f"NVMe command failed ReturnStatus=0x{int(hdr.ReturnStatus):X}",
                )
            if cmd.data_len > 0:
                off = int(hdr.DataFromDeviceBufferOffset)
                if off < DATA_OFFSET or off + cmd.data_len > len(out_buf):
                    raise AdminCommandError(
                        status_code=0,
                        opcode=cmd.opcode,
                        message=(
                            f"DataFromDeviceBufferOffset={off} invalid "
                            f"(buffer={len(out_buf)}, expected>={DATA_OFFSET})"
                        ),
                    )
                data = bytes(out_buf[off : off + cmd.data_len])
            else:
                data = b""
            record["status"] = 0
            return CommandResult(
                status=0,
                result_dw0=int(hdr.FixedProtocolReturnData),
                data=data,
            )

    def get_device_info(self) -> DeviceInfo:
        ctrl = identify_controller(self)
        return DeviceInfo(
            path=self.device_path,
            model=ctrl.mn,
            serial=ctrl.sn,
            firmware_rev=ctrl.fr,
            namespace_count=ctrl.nn,
            is_nvme=True,
        )

    def list_namespaces(self) -> list[int]:
        return active_namespace_list(self)

    def is_nvme(self) -> bool:
        if self._handle is None:
            raise DeviceError("device not opened")
        cmd = AdminCommand(
            opcode=int(AdminOpcode.IDENTIFY),
            cdw10=int(CNSValue.IDENTIFY_CONTROLLER),
            data_len=4096,
        )
        try:
            self.admin_passthru(cmd)
        except (AdminCommandError, CapabilityError, DeviceError):
            return False
        return True

    def capabilities(self) -> frozenset[str]:
        return frozenset({"device-io-control"})
