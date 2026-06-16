"""Unit tests for LinuxNvmeAdapter using patched module-level ioctl seam."""

from __future__ import annotations

import ctypes
import errno
import os as real_os
import types

import pytest

from nvme_sentinel.adapters import linux as linux_mod
from nvme_sentinel.adapters._linux_native import NvmePassthruCmd
from nvme_sentinel.hal.exceptions import (
    AdminCommandError,
    CapabilityError,
    DeviceError,
    DeviceNotFound,
    PermissionDenied,
)
from nvme_sentinel.hal.interface import AdminCommand


def test_open_raises_device_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """open maps ENOENT to DeviceNotFound."""
    adapter = linux_mod.LinuxNvmeAdapter("/dev/does-not-exist")

    def _raise_not_found(_path: str, _flags: int) -> int:
        raise FileNotFoundError(errno.ENOENT, "no such file")

    monkeypatch.setattr(
        linux_mod,
        "os",
        types.SimpleNamespace(open=_raise_not_found, O_RDWR=real_os.O_RDWR),
    )

    with pytest.raises(DeviceNotFound):
        adapter.open()


def test_admin_passthru_builds_expected_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """admin_passthru sends opcode/cdw10 via native struct build path."""
    adapter = linux_mod.LinuxNvmeAdapter("/dev/nvme0")
    adapter._fd = 11  # test-only fd injection
    cmd = AdminCommand(opcode=0x06, cdw10=0x01, data_len=16)

    captured: dict[str, int] = {}

    def _fake_ioctl(_fd: int, _req: int, native_cmd: NvmePassthruCmd) -> int:
        captured["opcode"] = int(native_cmd.opcode)
        captured["cdw10"] = int(native_cmd.cdw10)
        return 0

    monkeypatch.setattr(linux_mod, "_ioctl_call", _fake_ioctl)
    result = adapter.admin_passthru(cmd)

    assert captured["opcode"] == 0x06
    assert captured["cdw10"] == 0x01
    assert result.status == 0
    assert result.data == bytes(16)


def test_admin_passthru_returns_mock_written_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bytes written into pointed data buffer are returned to caller."""
    adapter = linux_mod.LinuxNvmeAdapter("/dev/nvme0")
    adapter._fd = 13  # test-only fd injection
    cmd = AdminCommand(opcode=0x06, cdw10=0x01, data_len=8)

    def _fake_ioctl(_fd: int, _req: int, native_cmd: NvmePassthruCmd) -> int:
        ptr = ctypes.cast(
            native_cmd.addr,
            ctypes.POINTER(ctypes.c_ubyte * 8),
        )
        payload = ptr.contents
        for idx in range(8):
            payload[idx] = idx + 1
        native_cmd.result = 0xA5A5
        return 0

    monkeypatch.setattr(linux_mod, "_ioctl_call", _fake_ioctl)
    result = adapter.admin_passthru(cmd)

    assert result.status == 0
    assert result.result_dw0 == 0xA5A5
    assert result.data == b"\x01\x02\x03\x04\x05\x06\x07\x08"


def test_admin_passthru_maps_eacces_to_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ioctl EACCES maps to PermissionDenied, not AdminCommandError."""
    adapter = linux_mod.LinuxNvmeAdapter("/dev/nvme0")
    adapter._fd = 17  # test-only fd injection
    cmd = AdminCommand(opcode=0x06, data_len=0)

    def _raise_eacces(_fd: int, _req: int, _native_cmd: NvmePassthruCmd) -> int:
        raise OSError(errno.EACCES, "access denied")

    monkeypatch.setattr(linux_mod, "_ioctl_call", _raise_eacces)

    with pytest.raises(PermissionDenied):
        adapter.admin_passthru(cmd)


def test_admin_passthru_nonzero_status_raises_admin_command_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive ioctl return is NVMe status and maps to AdminCommandError."""
    adapter = linux_mod.LinuxNvmeAdapter("/dev/nvme0")
    adapter._fd = 19  # test-only fd injection
    cmd = AdminCommand(opcode=0x06, data_len=0)

    def _nonzero_status(_fd: int, _req: int, _native_cmd: NvmePassthruCmd) -> int:
        return 0x4002

    monkeypatch.setattr(linux_mod, "_ioctl_call", _nonzero_status)

    with pytest.raises(AdminCommandError):
        adapter.admin_passthru(cmd)


def test_admin_passthru_maps_enotty_to_capability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ioctl ENOTTY maps to CapabilityError(not an NVMe device)."""
    adapter = linux_mod.LinuxNvmeAdapter("/dev/nvme0")
    adapter._fd = 21  # test-only fd injection

    def _raise_enotty(_fd: int, _req: int, _native_cmd: NvmePassthruCmd) -> int:
        raise OSError(errno.ENOTTY, "inappropriate ioctl for device")

    monkeypatch.setattr(linux_mod, "_ioctl_call", _raise_enotty)

    with pytest.raises(CapabilityError, match="not an NVMe device"):
        adapter.admin_passthru(AdminCommand(opcode=0x06, data_len=0))


def test_is_nvme_requires_opened_state() -> None:
    """is_nvme follows open-state contract and does not auto-open."""
    adapter = linux_mod.LinuxNvmeAdapter("/dev/nvme0")
    with pytest.raises(DeviceError, match="device not opened"):
        adapter.is_nvme()


def test_is_nvme_returns_false_when_admin_passthru_raises_admin_command_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """is_nvme returns False when Identify fails with AdminCommandError."""
    adapter = linux_mod.LinuxNvmeAdapter("/dev/nvme0")
    adapter._fd = 23  # test-only fd injection

    def _nonzero_status(_fd: int, _req: int, _native_cmd: NvmePassthruCmd) -> int:
        return 0x4002

    monkeypatch.setattr(linux_mod, "_ioctl_call", _nonzero_status)

    assert adapter.is_nvme() is False


def test_admin_passthru_zero_data_len_returns_empty_bytes_and_null_addr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """data_len==0 passes NULL addr and returns empty payload on success."""
    adapter = linux_mod.LinuxNvmeAdapter("/dev/nvme0")
    adapter._fd = 25  # test-only fd injection
    cmd = AdminCommand(opcode=0x06, data_len=0)
    captured: dict[str, int] = {}

    def _fake_ioctl(_fd: int, _req: int, native_cmd: NvmePassthruCmd) -> int:
        captured["addr"] = int(native_cmd.addr)
        captured["data_len"] = int(native_cmd.data_len)
        return 0

    monkeypatch.setattr(linux_mod, "_ioctl_call", _fake_ioctl)
    result = adapter.admin_passthru(cmd)

    assert captured["addr"] == 0
    assert captured["data_len"] == 0
    assert result.status == 0
    assert result.data == b""


def test_admin_passthru_fallback_on_eacces_uses_nvme_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PermissionDenied on ioctl engages nvme-cli for Identify Controller."""
    adapter = linux_mod.LinuxNvmeAdapter("/dev/nvme0")
    adapter._fd = 27
    adapter._nvme_cli._nvme_cli_path = "/usr/bin/nvme"
    adapter._nvme_cli._nvme_cli_major = 2

    def _raise_eacces(_fd: int, _req: int, _native_cmd: NvmePassthruCmd) -> int:
        raise OSError(errno.EACCES, "access denied")

    monkeypatch.setattr(linux_mod, "_ioctl_call", _raise_eacces)

    fake_identify = b"\x00" * 4096

    def _fake_id_ctrl(_path: str) -> bytes:
        return fake_identify

    monkeypatch.setattr(adapter._nvme_cli, "id_ctrl_raw", _fake_id_ctrl)

    cmd = AdminCommand(opcode=0x06, cdw10=0x01, data_len=4096)
    result = adapter.admin_passthru(cmd)

    assert result.status == 0
    assert result.data == fake_identify


def test_admin_passthru_does_not_fallback_on_admin_command_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AdminCommandError from ioctl is not retried via nvme-cli."""
    adapter = linux_mod.LinuxNvmeAdapter("/dev/nvme0")
    adapter._fd = 29
    adapter._nvme_cli._nvme_cli_path = "/usr/bin/nvme"
    adapter._nvme_cli._nvme_cli_major = 2

    def _nonzero_status(_fd: int, _req: int, _native_cmd: NvmePassthruCmd) -> int:
        return 0x4002

    monkeypatch.setattr(linux_mod, "_ioctl_call", _nonzero_status)

    with pytest.raises(AdminCommandError):
        adapter.admin_passthru(AdminCommand(opcode=0x06, cdw10=0x01, data_len=4096))
