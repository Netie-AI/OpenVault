"""Read-only adapter that replays host-exported JSON snapshots (VM / shared folder)."""

from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import unquote, urlparse

from nvme_sentinel.hal.base import BaseAdapter
from nvme_sentinel.hal.enums import AdminOpcode, CNSValue, LogPageID
from nvme_sentinel.hal.exceptions import AdminCommandError, CapabilityError, DeviceError
from nvme_sentinel.hal.interface import AdminCommand, CommandResult, DeviceInfo
from nvme_sentinel.models.identify import ControllerIdentify
from nvme_sentinel.snapshot.schema import DeviceSnapshot


def is_host_proxy_path(device_path: str | None) -> bool:
    """True if path selects HostProxyAdapter."""
    if not device_path:
        return False
    if device_path.startswith("host-proxy://"):
        return True
    lower = device_path.lower()
    return lower.endswith(".json") and "physicaldrive" not in lower and "/dev/" not in lower


def resolve_host_proxy_path(device_path: str) -> Path:
    """Resolve host-proxy:// URI or plain .json path to a filesystem path."""
    if device_path.startswith("host-proxy://"):
        parsed = urlparse(device_path)
        # host-proxy:///C:/path or host-proxy://C:/path
        raw = unquote(parsed.path)
        if parsed.netloc:
            raw = f"{parsed.netloc}{raw}"
        if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        return Path(raw)
    return Path(device_path)


class HostProxyAdapter(BaseAdapter):
    """
    Replays Identify/SMART from a DeviceSnapshot JSON file.

    Intended for VM guests reading host-collected read-only telemetry.
    """

    def __init__(self, snapshot_path: Path | str) -> None:
        super().__init__()
        self.snapshot_path = Path(snapshot_path)
        self.device_path = f"host-proxy://{self.snapshot_path.resolve()}"
        self._snapshot: DeviceSnapshot | None = None
        self._identify_bytes: bytes | None = None
        self._smart_bytes: bytes | None = None
        self._opened = False

    def open(self) -> None:
        if not self.snapshot_path.is_file():
            raise DeviceError(f"snapshot not found: {self.snapshot_path}")
        self._snapshot = DeviceSnapshot.model_validate_json(
            self.snapshot_path.read_text(encoding="utf-8")
        )
        if self._snapshot.identify_controller_b64:
            self._identify_bytes = base64.standard_b64decode(self._snapshot.identify_controller_b64)
        if self._snapshot.smart_health_b64:
            self._smart_bytes = base64.standard_b64decode(self._snapshot.smart_health_b64)
        self._opened = True

    def close(self) -> None:
        self._opened = False

    def admin_passthru(self, cmd: AdminCommand) -> CommandResult:
        if not self._opened or self._snapshot is None:
            raise DeviceError("host-proxy snapshot not opened")

        with self._timed(cmd) as record:
            if cmd.opcode == int(AdminOpcode.IDENTIFY):
                cns = cmd.cdw10 & 0xFF
                if cns == int(CNSValue.IDENTIFY_CONTROLLER):
                    if self._identify_bytes is None:
                        raise CapabilityError(
                            "snapshot has no identify_controller_b64; re-collect on host"
                        )
                    data = self._identify_bytes[: cmd.data_len or 4096]
                    record["status"] = 0
                    return CommandResult(status=0, result_dw0=0, data=data)
                if cns == int(CNSValue.ACTIVE_NAMESPACE_LIST):
                    data = (1).to_bytes(4, "little") + b"\x00" * (4096 - 4)
                    record["status"] = 0
                    return CommandResult(status=0, result_dw0=0, data=data)
                raise AdminCommandError(
                    status_code=0x0B,
                    opcode=cmd.opcode,
                    message="CNS not available in host-proxy snapshot",
                )

            if cmd.opcode == int(AdminOpcode.GET_LOG_PAGE):
                lid = cmd.cdw10 & 0xFF
                if lid == int(LogPageID.SMART_HEALTH):
                    if self._smart_bytes is None:
                        raise CapabilityError(
                            "snapshot has no smart_health_b64; re-collect on host"
                        )
                    data = self._smart_bytes[: cmd.data_len or 512]
                    record["status"] = 0
                    return CommandResult(status=0, result_dw0=0, data=data)
                raise AdminCommandError(
                    status_code=0x0B,
                    opcode=cmd.opcode,
                    message="log page not in host-proxy snapshot",
                )

            raise CapabilityError(f"opcode 0x{cmd.opcode:02X} not supported by host-proxy")

    def get_device_info(self) -> DeviceInfo:
        snap = self._snapshot
        if snap is None:
            raise DeviceError("host-proxy snapshot not opened")
        if snap.device_info:
            di = snap.device_info
            return DeviceInfo(
                path=self.device_path,
                model=str(di.get("model", "host-proxy")),
                serial=str(di.get("serial", "")),
                firmware_rev=str(di.get("firmware_rev", "")),
                namespace_count=int(di.get("namespace_count", 1)),
                is_nvme=bool(di.get("is_nvme", True)),
            )
        if self._identify_bytes:
            ctrl = ControllerIdentify.from_bytes(self._identify_bytes)
            return DeviceInfo(
                path=self.device_path,
                model=ctrl.mn,
                serial=ctrl.sn,
                firmware_rev=ctrl.fr,
                namespace_count=ctrl.nn,
                is_nvme=True,
            )
        return DeviceInfo(
            path=self.device_path,
            model="host-proxy",
            serial="",
            firmware_rev="",
            namespace_count=1,
            is_nvme=True,
        )

    def list_namespaces(self) -> list[int]:
        return [1]

    def is_nvme(self) -> bool:
        if self._snapshot and self._snapshot.device_info:
            return bool(self._snapshot.device_info.get("is_nvme", True))
        return self._identify_bytes is not None

    def capabilities(self) -> frozenset[str]:
        return frozenset({"host-proxy"})
