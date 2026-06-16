"""Collect read-only snapshots from live devices."""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from nvme_sentinel.commands.identify import identify_controller
from nvme_sentinel.hal.enums import AdminOpcode, CNSValue, LogPageID
from nvme_sentinel.hal.factory import get_adapter
from nvme_sentinel.hal.interface import AdminCommand
from nvme_sentinel.snapshot.schema import DeviceSnapshot
from nvme_sentinel.telemetry.read import read_smart


def encode_raw(data: bytes) -> str:
    """Base64-encode binary NVMe payloads for JSON snapshots."""
    import base64

    return base64.standard_b64encode(data).decode("ascii")


def _fetch_raw_passthrough(
    device_path: str,
    *,
    mock: bool,
    force: str | None,
) -> tuple[str | None, str | None]:
    """Return (identify_b64, smart_b64) from admin passthrough when available."""
    adapter_force: str | None = force if force else ("mock" if mock else None)
    identify_b64: str | None = None
    smart_b64: str | None = None
    try:
        with get_adapter(device_path=device_path, force=adapter_force) as dev:  # type: ignore[arg-type]
            id_cmd = AdminCommand(
                opcode=int(AdminOpcode.IDENTIFY),
                cdw10=int(CNSValue.IDENTIFY_CONTROLLER),
                data_len=4096,
            )
            id_res = dev.admin_passthru(id_cmd)
            if id_res.status == 0 and len(id_res.data) >= 4096:
                identify_b64 = encode_raw(id_res.data[:4096])

            smart_cdw10 = int(LogPageID.SMART_HEALTH) | (127 << 16)
            sm_cmd = AdminCommand(
                opcode=int(AdminOpcode.GET_LOG_PAGE),
                nsid=0xFFFFFFFF,
                cdw10=smart_cdw10,
                data_len=512,
            )
            sm_res = dev.admin_passthru(sm_cmd)
            if sm_res.status == 0 and len(sm_res.data) >= 512:
                smart_b64 = encode_raw(sm_res.data[:512])
    except Exception:
        return None, None
    return identify_b64, smart_b64


def collect_snapshot(
    device_path: str,
    *,
    mock: bool = False,
    force: str | None = None,
) -> DeviceSnapshot:
    """
    Gather Identify + SMART (or WMI subset) into a JSON-serializable snapshot.

    Does not write to the device — read-only admin queries only.
    """
    adapter_force: str | None = force if force else ("mock" if mock else None)
    smart_result = read_smart(device_path, mock=mock, force=adapter_force)

    identify_dict: dict[str, int | str] | None = None
    device_info_dict: dict[str, str | int | bool] | None = None
    caps: list[str] = []

    identify_b64, smart_b64 = _fetch_raw_passthrough(device_path, mock=mock, force=adapter_force)

    if smart_result.smart is not None or mock:
        try:
            with get_adapter(device_path=device_path, force=adapter_force) as dev:  # type: ignore[arg-type]
                caps = sorted(dev.capabilities())
                ctrl = identify_controller(dev)
                identify_dict = {
                    "vid": ctrl.vid,
                    "ssvid": ctrl.ssvid,
                    "sn": ctrl.sn,
                    "mn": ctrl.mn,
                    "fr": ctrl.fr,
                    "nn": ctrl.nn,
                    "cntlid": ctrl.cntlid,
                }
                info = dev.get_device_info()
                device_info_dict = {
                    "path": info.path,
                    "model": info.model,
                    "serial": info.serial,
                    "firmware_rev": info.firmware_rev,
                    "namespace_count": info.namespace_count,
                    "is_nvme": info.is_nvme,
                }
        except Exception:
            identify_dict = None

    smart_dict = smart_result.smart.to_dict() if smart_result.smart else None

    if mock and smart_dict is None:
        smart_result = read_smart(device_path, mock=True)
        smart_dict = smart_result.smart.to_dict() if smart_result.smart else None

    return DeviceSnapshot(
        collected_at=datetime.now(timezone.utc),
        device_path=device_path,
        platform=sys.platform,
        telemetry_source=smart_result.source,
        readonly=True,
        adapter_capabilities=caps,
        device_info=device_info_dict,
        identify_controller=identify_dict,
        smart_health=smart_dict,
        wmi_fallback=smart_result.wmi_counters,
        identify_controller_b64=identify_b64,
        smart_health_b64=smart_b64,
        notes=smart_result.message,
    )


def snapshot_to_json_bytes(snapshot: DeviceSnapshot) -> bytes:
    """Serialize snapshot for file export."""
    return snapshot.model_dump_json(indent=2).encode("utf-8")


def load_snapshot(path: str) -> DeviceSnapshot:
    """Load snapshot from JSON file."""
    from pathlib import Path

    return DeviceSnapshot.model_validate_json(Path(path).read_text(encoding="utf-8"))
