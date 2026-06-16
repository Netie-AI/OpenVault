"""Deterministic mock NVMe adapter using fixture-backed admin responses."""

from __future__ import annotations

from pathlib import Path

from nvme_sentinel.hal.base import BaseAdapter
from nvme_sentinel.hal.enums import AdminOpcode, CNSValue, LogPageID
from nvme_sentinel.hal.exceptions import AdminCommandError, CapabilityError, DeviceError
from nvme_sentinel.hal.interface import AdminCommand, CommandResult, DeviceInfo
from nvme_sentinel.models.identify import ControllerIdentify

FIXTURES_DIR = Path("tests/fixtures")


class MockNvmeAdapter(BaseAdapter):
    """Fixture-backed adapter that deterministically replays NVMe admin payloads."""

    def __init__(
        self,
        device_path: str = "/dev/mock-nvme0",
        identify_ctrl_path: Path | None = None,
        identify_ns_path: Path | None = None,
        smart_path: Path | None = None,
    ) -> None:
        """Initialize mock adapter with optional fixture override paths."""
        super().__init__()
        self.device_path = device_path
        self._identify_ctrl_path = identify_ctrl_path or (
            FIXTURES_DIR / "identify_ctrl_generic.bin"
        )
        self._identify_ns_path = identify_ns_path or (FIXTURES_DIR / "identify_ns.bin")
        self._smart_path = smart_path or (FIXTURES_DIR / "smart_healthy.bin")
        self._opened = False
        # TODO(T9.1): resolve fixture paths relative to __file__ or importlib.resources
        # so `nvme-sentinel demo` works from any working directory.
        self._identify_controller_bytes = self._identify_ctrl_path.read_bytes()
        self._identify_namespace_bytes = self._identify_ns_path.read_bytes()
        self._smart_bytes = self._smart_path.read_bytes()
        fw_slot = bytearray(512)
        fw_slot[0] = 0x01
        fw_slot[8:16] = b"GS01GR00"
        self._firmware_slot_bytes = bytes(fw_slot)

    def open(self) -> None:
        """Open mock adapter state machine."""
        self._opened = True

    def close(self) -> None:
        """Close mock adapter state machine."""
        self._opened = False

    def admin_passthru(self, cmd: AdminCommand) -> CommandResult:
        """Dispatch fixture response by opcode and command dword content."""
        if not self._opened:
            raise DeviceError(f"device '{self.device_path}' is not open")

        with self._timed(cmd) as record:
            if cmd.opcode == int(AdminOpcode.IDENTIFY):
                data = self._handle_identify(cmd)
            elif cmd.opcode == int(AdminOpcode.GET_LOG_PAGE):
                data = self._handle_log_page(cmd)
            else:
                raise CapabilityError(f"opcode 0x{cmd.opcode:02X} is not supported by mock adapter")

            result = CommandResult(status=0, result_dw0=0, data=data)
            record["status"] = result.status
            return result

    def get_device_info(self) -> DeviceInfo:
        """Return identity details parsed from Identify Controller fixture bytes."""
        ctrl = ControllerIdentify.from_bytes(self._identify_controller_bytes)
        return DeviceInfo(
            path=self.device_path,
            model=ctrl.mn,
            serial=ctrl.sn,
            firmware_rev=ctrl.fr,
            namespace_count=ctrl.nn,
            is_nvme=True,
        )

    def list_namespaces(self) -> list[int]:
        """Return active namespace list represented by fixtures."""
        return [1]

    def is_nvme(self) -> bool:
        """Return mock NVMe capability."""
        return True

    def capabilities(self) -> frozenset[str]:
        """Return capability set exposed by this adapter."""
        return frozenset({"mock"})

    def _handle_identify(self, cmd: AdminCommand) -> bytes:
        """Handle Identify admin opcode using CNS in cdw10 bits 7:0."""
        cns = cmd.cdw10 & 0xFF
        if cns == int(CNSValue.IDENTIFY_CONTROLLER):
            return self._slice_len(self._identify_controller_bytes, cmd.data_len)
        if cns == int(CNSValue.IDENTIFY_NAMESPACE):
            return self._slice_len(self._identify_namespace_bytes, cmd.data_len)
        if cns == int(CNSValue.ACTIVE_NAMESPACE_LIST):
            payload = (1).to_bytes(4, "little") + bytes(4092)
            return self._slice_len(payload, cmd.data_len)
        raise AdminCommandError(
            status_code=0x0B,
            opcode=int(AdminOpcode.IDENTIFY),
            message=f"CNS 0x{cns:02X} not mocked",
        )

    def _handle_log_page(self, cmd: AdminCommand) -> bytes:
        """Handle Get Log Page opcode using LID in cdw10 bits 7:0."""
        lid = cmd.cdw10 & 0xFF
        if lid == int(LogPageID.SMART_HEALTH):
            return self._slice_len(self._smart_bytes, cmd.data_len)
        if lid == int(LogPageID.ERROR_INFO):
            return self._slice_len(bytes(max(cmd.data_len, 0)), cmd.data_len)
        if lid == int(LogPageID.FIRMWARE_SLOT):
            return self._slice_len(self._firmware_slot_bytes, cmd.data_len)
        raise AdminCommandError(
            status_code=0x0B,
            opcode=int(AdminOpcode.GET_LOG_PAGE),
            message=f"LID 0x{lid:02X} not mocked",
        )

    @staticmethod
    def _slice_len(payload: bytes, data_len: int) -> bytes:
        """Match command-requested data length without introducing randomness."""
        if data_len <= 0:
            return b""
        return payload[:data_len]
