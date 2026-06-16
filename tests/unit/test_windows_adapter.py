"""Unit tests for Windows storage path (mock _win32 seam; runs on Linux CI)."""

from __future__ import annotations

import ctypes
import sys

import pytest

from nvme_sentinel.adapters import windows as win_mod
from nvme_sentinel.adapters._windows_native import (
    IOCTL_STORAGE_PROTOCOL_COMMAND,
    STORAGE_PROTOCOL_SPECIFIC_NVME_ADMIN_COMMAND,
    StorageProtocolCommandHeader,
    build_protocol_command,
)
from nvme_sentinel.hal.exceptions import (
    AdminCommandError,
    CapabilityError,
    DeviceNotFound,
    PermissionDenied,
)
from nvme_sentinel.hal.interface import AdminCommand, CommandResult


def test_struct_size() -> None:
    assert ctypes.sizeof(StorageProtocolCommandHeader) == 80


def test_ioctl_constant() -> None:
    assert IOCTL_STORAGE_PROTOCOL_COMMAND == 0x002DD3C8


def test_build_protocol_command_length() -> None:
    cmd = AdminCommand(opcode=0x06, data_len=4096)
    blob = build_protocol_command(cmd)
    assert len(blob) == 208 + 4096


def test_build_protocol_command_opcode() -> None:
    cmd = AdminCommand(opcode=0x06, data_len=0)
    blob = build_protocol_command(cmd)
    assert blob[80] == 0x06


def test_build_protocol_command_nsid() -> None:
    cmd = AdminCommand(opcode=0x02, nsid=0x12345678)
    blob = build_protocol_command(cmd)
    assert blob[84:88] == (0x12345678).to_bytes(4, "little")


def test_build_protocol_command_command_specific() -> None:
    buf = build_protocol_command(AdminCommand(opcode=0x06, data_len=4096))
    hdr = StorageProtocolCommandHeader.from_buffer_copy(buf[:80])
    assert hdr.CommandSpecific == STORAGE_PROTOCOL_SPECIFIC_NVME_ADMIN_COMMAND
    assert hdr.ErrorInfoLength == 64
    assert hdr.ErrorInfoOffset == 144
    assert hdr.DataFromDeviceBufferOffset == 208


class _RecordingWin32:
    create_file_calls: list[tuple[str, int, int, int]]

    def __init__(self) -> None:
        self.create_file_calls = []

    def create_file(self, path: str, access: int, share: int, disp: int) -> int:
        self.create_file_calls.append((path, access, share, disp))
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


def test_open_non_windows_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _RecordingWin32()
    monkeypatch.setattr(win_mod, "_win32", rec)
    monkeypatch.setattr(sys, "platform", "linux")
    adapter = win_mod.WindowsStorageAdapter(r"\\.\PhysicalDrive0")
    with pytest.raises(CapabilityError, match="Windows"):
        adapter.open()
    assert rec.create_file_calls == []


def test_open_file_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Win32:
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
            return win_mod.ERROR_FILE_NOT_FOUND

    monkeypatch.setattr(win_mod, "_win32", _Win32())
    monkeypatch.setattr(sys, "platform", "win32")
    adapter = win_mod.WindowsStorageAdapter(r"\\.\PhysicalDriveMissing")
    with pytest.raises(DeviceNotFound):
        adapter.open()


def test_open_access_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win_mod, "_volume_path_for_physical", lambda _p: None)

    class _Win32:
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
            return win_mod.ERROR_ACCESS_DENIED

    monkeypatch.setattr(win_mod, "_win32", _Win32())
    monkeypatch.setattr(sys, "platform", "win32")
    adapter = win_mod.WindowsStorageAdapter(r"\\.\PhysicalDrive0")
    with pytest.raises(PermissionDenied):
        adapter.open()


def test_admin_passthru_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Win32:
        def create_file(self, path: str, access: int, share: int, disp: int) -> int:
            return 999

        def device_io_control(
            self,
            handle: int,
            code: int,
            in_buf: bytes | bytearray,
            out_buf: bytearray,
        ) -> tuple[bool, int]:
            hdr = StorageProtocolCommandHeader()
            hdr.ReturnStatus = 0
            hdr.FixedProtocolReturnData = 0x11223344
            ctypes.memmove((ctypes.c_ubyte * 80).from_buffer(out_buf), ctypes.addressof(hdr), 80)
            return True, len(out_buf)

        def close_handle(self, handle: int) -> None:
            return None

        def get_last_error(self) -> int:
            return 0

    monkeypatch.setattr(win_mod, "_win32", _Win32())
    adapter = win_mod.WindowsStorageAdapter(r"\\.\PhysicalDrive0")
    adapter._handle = 999
    cmd = AdminCommand(opcode=0x06, data_len=0)
    result = adapter.admin_passthru(cmd)
    assert isinstance(result, CommandResult)
    assert result.status == 0
    assert result.result_dw0 == 0x11223344


def test_admin_passthru_ioctl_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Win32:
        def create_file(self, path: str, access: int, share: int, disp: int) -> int:
            return 1

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
            return 0x1234

    monkeypatch.setattr(win_mod, "_win32", _Win32())
    adapter = win_mod.WindowsStorageAdapter(r"\\.\PhysicalDrive0")
    adapter._handle = 1
    with pytest.raises(AdminCommandError):
        adapter.admin_passthru(AdminCommand(opcode=0x06, data_len=0))
