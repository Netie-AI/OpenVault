"""Linux NVMe adapter using ioctl passthrough from implementation_plan.md §4.5."""

from __future__ import annotations

import ctypes
import errno
import os
from collections.abc import Callable
from typing import cast

import structlog

from nvme_sentinel.adapters._linux_native import NVME_IOCTL_ADMIN_CMD, build_admin_cmd
from nvme_sentinel.adapters._nvme_cli import NvmeCliSupport
from nvme_sentinel.hal.base import BaseAdapter
from nvme_sentinel.hal.enums import AdminOpcode, CNSValue, LogPageID
from nvme_sentinel.hal.exceptions import (
    AdminCommandError,
    CapabilityError,
    DeviceError,
    DeviceNotFound,
    PermissionDenied,
)
from nvme_sentinel.hal.interface import AdminCommand, CommandResult, DeviceInfo
from nvme_sentinel.models.identify import ControllerIdentify

_LOG = structlog.get_logger()

try:
    import fcntl

    _ioctl_call = cast(
        Callable[..., int],
        fcntl.ioctl,  # type: ignore[attr-defined]  # provided on Linux, absent from some stubs on Windows
    )
except ModuleNotFoundError:  # pragma: no cover - exercised on non-Linux runtimes

    def _ioctl_call(*_a: object, **_kw: object) -> int:
        raise CapabilityError("LinuxNvmeAdapter requires Linux (fcntl unavailable)")


class LinuxNvmeAdapter(BaseAdapter):
    """Linux adapter for NVMe admin passthrough via NVME_IOCTL_ADMIN_CMD."""

    def __init__(self, device_path: str) -> None:
        """Store Linux character/block device path and initialize unopened state."""
        super().__init__()
        self.device_path = device_path
        self._fd: int | None = None
        self._nvme_cli = NvmeCliSupport()

    def open(self) -> None:
        """Open device fd for read/write admin passthrough."""
        if self._fd is not None:
            return
        try:
            self._fd = os.open(self.device_path, os.O_RDWR)
        except FileNotFoundError as exc:
            raise DeviceNotFound(f"device not found: {self.device_path}") from exc
        except PermissionError as exc:
            raise PermissionDenied(f"permission denied opening {self.device_path}") from exc
        except OSError as exc:
            err_no = exc.errno if exc.errno is not None else -1
            err = errno.errorcode.get(err_no, str(err_no))
            raise DeviceError(f"failed to open {self.device_path}: errno={err}") from exc

    def close(self) -> None:
        """Close opened fd; idempotent when already closed."""
        if self._fd is None:
            return
        try:
            os.close(self._fd)
        finally:
            self._fd = None

    def admin_passthru(self, cmd: AdminCommand) -> CommandResult:
        """Submit one admin command through Linux ioctl and return CQE/result payload."""
        if self._fd is None:
            raise DeviceError("device not opened")

        try:
            return self._admin_passthru_ioctl(cmd)
        except (PermissionDenied, CapabilityError) as exc:
            fallback = self._try_nvme_cli_fallback(cmd, reason=type(exc).__name__)
            if fallback is not None:
                return fallback
            raise

    def _admin_passthru_ioctl(self, cmd: AdminCommand) -> CommandResult:
        data_buf: ctypes.Array[ctypes.c_ubyte] | None
        if cmd.data_len == 0:
            data_buf = None
            addr = 0
        else:
            data_buf = (ctypes.c_ubyte * cmd.data_len)()
            addr = ctypes.addressof(data_buf)

        # build_admin_cmd sets a placeholder addr for ctypes; authoritative addr is assigned
        # below so data_len==0 uses NULL (addr=0) per NVMe admin passthrough semantics.
        fallback_buf = (ctypes.c_ubyte * 1)()
        native_cmd = build_admin_cmd(cmd, data_buf if data_buf is not None else fallback_buf)
        native_cmd.addr = addr
        native_cmd.data_len = cmd.data_len

        with self._timed(cmd) as record:
            try:
                ret = _ioctl_call(self._fd, NVME_IOCTL_ADMIN_CMD, native_cmd)
            except OSError as exc:
                err_no = exc.errno
                if err_no in (errno.EACCES, errno.EPERM):
                    err_name = errno.errorcode.get(err_no, err_no)
                    raise PermissionDenied(
                        f"permission denied issuing admin command: errno={err_name}"
                    ) from exc
                if err_no == errno.ENOTTY:
                    raise CapabilityError("not an NVMe device") from exc
                err_name = errno.errorcode.get(err_no if err_no is not None else -1, str(err_no))
                raise DeviceError(f"ioctl admin passthru failed: errno={err_name}") from exc

            if ret > 0:
                raise AdminCommandError(
                    status_code=ret,
                    opcode=cmd.opcode,
                    message="admin passthru returned non-zero NVMe status",
                )

            record["status"] = 0
            data = b"" if data_buf is None else bytes(data_buf)[: cmd.data_len]
            return CommandResult(status=0, result_dw0=native_cmd.result, data=data)

    def _try_nvme_cli_fallback(
        self,
        cmd: AdminCommand,
        *,
        reason: str,
    ) -> CommandResult | None:
        if not self._nvme_cli.available():
            return None
        data = self._dispatch_nvme_cli(cmd)
        if data is None:
            return None
        _LOG.warning("nvme_cli_fallback_engaged", reason=reason, opcode=cmd.opcode)
        return CommandResult(status=0, result_dw0=0, data=data)

    def _dispatch_nvme_cli(self, cmd: AdminCommand) -> bytes | None:
        if cmd.opcode == int(AdminOpcode.IDENTIFY):
            cns = cmd.cdw10 & 0xFF
            if cns == int(CNSValue.IDENTIFY_CONTROLLER):
                return self._nvme_cli.id_ctrl_raw(self.device_path)
            if cns == int(CNSValue.IDENTIFY_NAMESPACE) and cmd.nsid > 0:
                return self._nvme_cli.id_ns_raw(self.device_path, cmd.nsid)
        if cmd.opcode == int(AdminOpcode.GET_LOG_PAGE):
            lid = cmd.cdw10 & 0xFF
            if lid == int(LogPageID.SMART_HEALTH):
                return self._nvme_cli.get_smart_raw(self.device_path)
        return None

    def get_device_info(self) -> DeviceInfo:
        """Issue Identify Controller and project core identity fields into DeviceInfo."""
        identify_cmd = AdminCommand(
            opcode=int(AdminOpcode.IDENTIFY),
            cdw10=int(CNSValue.IDENTIFY_CONTROLLER),
            data_len=4096,
        )
        result = self.admin_passthru(identify_cmd)
        ctrl = ControllerIdentify.from_bytes(result.data)
        return DeviceInfo(
            path=self.device_path,
            model=ctrl.mn,
            serial=ctrl.sn,
            firmware_rev=ctrl.fr,
            namespace_count=ctrl.nn,
            is_nvme=True,
        )

    def list_namespaces(self) -> list[int]:
        """Return zero-terminated NSID list via Identify CNS 0x02."""
        cmd = AdminCommand(
            opcode=int(AdminOpcode.IDENTIFY),
            cdw10=int(CNSValue.ACTIVE_NAMESPACE_LIST),
            data_len=4096,
        )
        result = self.admin_passthru(cmd)
        namespaces: list[int] = []
        for offset in range(0, len(result.data), 4):
            nsid = int.from_bytes(result.data[offset : offset + 4], "little")
            if nsid == 0:
                break
            namespaces.append(nsid)
        return namespaces

    def is_nvme(self) -> bool:
        """Probe NVMe support by issuing Identify Controller on opened device."""
        if self._fd is None:
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
        """Return currently supported Linux adapter capabilities."""
        caps: set[str] = {"ioctl"}
        if self._nvme_cli.available():
            caps.add(self._nvme_cli.capability_token())
        return frozenset(caps)
