"""Read-only NVMe telemetry over the in-repo nvme_sentinel engine.

Contract for every payload here:

* ``source`` is ``"live"`` when the request targeted real hardware and ``"mock"``
  when fixtures were used. Mock is never selected implicitly — a caller has to
  ask for it (``mock=true`` or ``OPENVAULT_SENTINEL_MOCK=1``). A live read that
  fails returns ``ok=false`` with a reason, it does not quietly become fixtures.
* ``degraded_reason`` is set whenever the adapter ladder dropped a rung, so the
  UI can render the badge instead of pretending the numbers are complete.

Nothing in this module writes to a device: Identify, Get Log Page and the WMI
reliability counters are all read-only, matching nvme_sentinel's own guarantee.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from nvme_sentinel.commands.identify import (
    active_namespace_list,
    identify_controller,
    identify_namespace,
)
from nvme_sentinel.commands.log_pages import get_error_info_log, get_smart_health
from nvme_sentinel.hal.exceptions import (
    AdminCommandError,
    CapabilityError,
    DeviceNotFound,
    NvmeSentinelError,
    ParseError,
    PermissionDenied,
)
from nvme_sentinel.hal.factory import get_adapter
from nvme_sentinel.hal.interface import StorageInterface
from nvme_sentinel.inventory.discovery import list_devices
from nvme_sentinel.inventory.models import InventoryDevice
from nvme_sentinel.snapshot.schema import DeviceSnapshot
from nvme_sentinel.telemetry.source import TelemetrySource

from openmw.openvault.sentinel.fixtures import MOCK_DEVICE_PATH, mock_adapter, resolve_fixture_dir
from openmw.openvault.sentinel.privilege import elevation_hint, is_elevated

MOCK_ENV = "OPENVAULT_SENTINEL_MOCK"

# Get-PhysicalDisk shells out to PowerShell; a short TTL keeps the console
# responsive without ever serving a stale device list to a human.
_INVENTORY_TTL_S = 10.0

# SMART reports how many error-log entries exist; cap the read so a controller
# claiming thousands cannot turn one HTTP request into a multi-megabyte transfer.
_MAX_ERROR_ENTRIES = 64

# Fields the OS hands us without elevation (Get-PhysicalDisk +
# MSFT_StorageReliabilityCounter). The reliability counters are vendor-optional:
# plenty of consumer NVMe drives return null for every one of them.
FIELDS_WITHOUT_ADMIN: tuple[str, ...] = (
    "device_path",
    "model",
    "serial",
    "size_bytes",
    "bus_type",
    "media_type",
    "drive_letters",
    "wmi.Temperature",
    "wmi.Wear",
    "wmi.PowerOnHours",
    "wmi.ReadErrorsTotal",
    "wmi.WriteErrorsTotal",
    "wmi.StartStopCycleCount",
)

# Everything below comes out of NVMe admin passthrough (Identify / Get Log Page),
# which needs a raw device handle, which needs Administrator on Windows.
FIELDS_REQUIRING_ADMIN: tuple[str, ...] = (
    "critical_warning",
    "composite_temperature_celsius",
    "available_spare",
    "available_spare_threshold",
    "percentage_used",
    "data_units_read",
    "data_units_written",
    "host_read_commands",
    "host_write_commands",
    "controller_busy_time_minutes",
    "power_cycles",
    "power_on_hours",
    "unsafe_shutdowns",
    "media_and_data_integrity_errors",
    "number_of_error_information_log_entries",
    "warning_composite_temp_time_minutes",
    "critical_composite_temp_time_minutes",
    "identify_controller",
    "identify_namespace",
    "error_information_log",
)

_inventory_cache: tuple[float, list[InventoryDevice]] | None = None


@dataclass(frozen=True, slots=True)
class Degradation:
    """Why a read could not use the best available adapter."""

    reason: str
    needs_admin: bool


def env_mock_enabled() -> bool:
    """True when the environment pins this process to fixture data."""
    return os.environ.get(MOCK_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def use_mock(mock: bool | None = None) -> bool:
    """Resolve the mock switch: explicit request wins, else the env pin."""
    if mock is not None:
        return mock
    return env_mock_enabled()


def _inventory(*, refresh: bool = False) -> list[InventoryDevice]:
    global _inventory_cache
    now = time.monotonic()
    if not refresh and _inventory_cache is not None:
        stamped, cached = _inventory_cache
        if now - stamped < _INVENTORY_TTL_S:
            return cached
    try:
        devices = list_devices()
    except (OSError, ValueError):  # PowerShell missing or emitting non-JSON
        devices = []
    _inventory_cache = (now, devices)
    return devices


def default_device_path(*, mock: bool) -> str | None:
    """Pick the drive the console should show first: boot NVMe > any NVMe > any disk."""
    if mock:
        return MOCK_DEVICE_PATH
    devices = _inventory()
    if not devices:
        return None
    nvme = [d for d in devices if d.is_nvme]
    pool = nvme or devices
    for dev in pool:
        letters = {x.upper().rstrip(":") for x in dev.drive_letters}
        if "C" in letters or dev.device_path.endswith("PhysicalDrive0"):
            return dev.device_path
    return pool[0].device_path


def classify_failure(exc: Exception, device_path: str) -> Degradation:
    """Turn an adapter failure into a sentence a non-engineer can act on."""
    if isinstance(exc, PermissionDenied):
        return Degradation(
            reason=(
                f"needs Administrator — opening {device_path} for NVMe passthrough "
                f"requires an elevated process ({elevation_hint()})"
            ),
            needs_admin=True,
        )
    if isinstance(exc, DeviceNotFound):
        return Degradation(reason=f"device not found: {device_path}", needs_admin=False)
    if isinstance(exc, AdminCommandError):
        if exc.status_code == 1:
            return Degradation(
                reason=(
                    "storage driver rejected IOCTL_STORAGE_PROTOCOL_COMMAND — "
                    "this controller does not expose NVMe passthrough to the OS"
                ),
                needs_admin=False,
            )
        return Degradation(reason=str(exc), needs_admin=False)
    if isinstance(exc, CapabilityError):
        return Degradation(reason=str(exc), needs_admin=False)
    if isinstance(exc, ParseError):
        return Degradation(reason=f"payload did not parse: {exc}", needs_admin=False)
    return Degradation(reason=f"{type(exc).__name__}: {exc}", needs_admin=False)


@contextmanager
def open_device(device_path: str, *, mock: bool) -> Iterator[StorageInterface]:
    """Open the mock adapter or the platform adapter for one read-only session."""
    dev: StorageInterface = mock_adapter(device_path) if mock else get_adapter(device_path)
    dev.open()
    try:
        yield dev
    finally:
        dev.close()


def _wmi_counters(device_path: str) -> dict[str, int | str] | None:
    """MSFT_StorageReliabilityCounter subset — real device data, no admin needed."""
    if sys.platform != "win32":
        return None
    try:
        from nvme_sentinel.adapters._wmi_fallback import (
            disk_number_from_path,
            get_reliability_counters,
        )

        return get_reliability_counters(disk_number_from_path(device_path))
    except (NvmeSentinelError, ValueError, OSError):
        return None


def _base(*, mock: bool, device_path: str | None) -> dict[str, Any]:
    return {
        "source": "mock" if mock else "live",
        "device": device_path,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "readonly": True,
    }


def _no_device_payload(*, mock: bool) -> dict[str, Any]:
    payload = _base(mock=mock, device_path=None)
    payload.update(
        {
            "ok": False,
            "degraded": True,
            "degraded_reason": (
                "no storage device detected — Get-PhysicalDisk returned nothing "
                "(pass ?mock=true to inspect the fixture device instead)"
            ),
        }
    )
    return payload


# --- device inventory -------------------------------------------------------


def devices_payload(*, mock: bool = False, refresh: bool = False) -> dict[str, Any]:
    """Enumerate storage devices. Works without elevation on both platforms."""
    if mock:
        payload = _base(mock=True, device_path=MOCK_DEVICE_PATH)
        _fixtures, synthesized = resolve_fixture_dir()
        payload.update(
            {
                "ok": True,
                "degraded": True,
                "degraded_reason": (
                    "fixture device — synthesized payloads"
                    if synthesized
                    else "fixture device — nvme_sentinel test vectors, not your hardware"
                ),
                "count": 1,
                "devices": [
                    {
                        "device_path": MOCK_DEVICE_PATH,
                        "friendly_name": "OpenVault Mock NVMe",
                        "model": "OpenVault Mock NVMe Controller",
                        "serial": "OPENVAULT-MOCK-0001",
                        "size_bytes": 512_110_190_592,
                        "media_type": "SSD",
                        "bus_type": "NVMe",
                        "is_nvme": True,
                        "drive_letters": [],
                        "suggested_telemetry": ["mock"],
                        "is_default": True,
                    }
                ],
            }
        )
        return payload

    devices = _inventory(refresh=refresh)
    chosen = default_device_path(mock=False)
    payload = _base(mock=False, device_path=chosen)
    rows: list[dict[str, Any]] = []
    for dev in devices:
        row = dev.model_dump()
        row["is_default"] = dev.device_path == chosen
        rows.append(row)
    payload.update(
        {
            "ok": bool(rows),
            "count": len(rows),
            "devices": rows,
            "degraded": not rows,
            "degraded_reason": None
            if rows
            else "no storage device detected — Get-PhysicalDisk returned nothing",
        }
    )
    return payload


# --- capabilities: the honesty endpoint -------------------------------------


def capabilities_payload(*, device: str | None = None, mock: bool = False) -> dict[str, Any]:
    """Report which adapter actually bound, whether we are elevated, and what that costs.

    This is the endpoint the UI badge reads. It performs a real Identify probe —
    a capability map derived from guesswork would be exactly the kind of
    plausible-looking fiction this rebuild exists to delete.
    """
    elevated = is_elevated()
    device_path = device or default_device_path(mock=mock)
    payload = _base(mock=mock, device_path=device_path)
    fixture_dir, synthesized = resolve_fixture_dir()

    probe: dict[str, Any] = {
        "adapter": None,
        "adapter_capabilities": [],
        "telemetry_source": None,
        "passthrough_ok": False,
        "probe_error": None,
    }
    degraded_reason: str | None = None
    needs_admin = False

    if device_path is None:
        probe["probe_error"] = "no device to probe"
        degraded_reason = "no storage device detected"
    else:
        try:
            with open_device(device_path, mock=mock) as dev:
                caps = sorted(dev.capabilities())
                identify_controller(dev)  # cheapest read that proves passthrough works
                probe.update(
                    {
                        "adapter": type(dev).__name__,
                        "adapter_capabilities": caps,
                        "telemetry_source": _telemetry_source(caps).value,
                        "passthrough_ok": True,
                    }
                )
                if mock:
                    degraded_reason = (
                        "fixture adapter — every number below is a test vector, not your drive"
                    )
        except (NvmeSentinelError, OSError) as exc:
            failure = classify_failure(exc, device_path)
            needs_admin = failure.needs_admin
            probe["probe_error"] = failure.reason
            counters = _wmi_counters(device_path)
            if counters:
                probe.update(
                    {
                        "adapter": "WmiReliabilityCounters",
                        "adapter_capabilities": ["wmi"],
                        "telemetry_source": TelemetrySource.WMI.value,
                    }
                )
                degraded_reason = (
                    f"adapter fell back to WMI ({failure.reason}) — endurance "
                    "(percentage_used, data_units_written, available_spare) and the "
                    "NVMe error log are unavailable on this path"
                )
            else:
                degraded_reason = failure.reason

    payload.update(probe)
    payload.update(
        {
            "ok": bool(probe["passthrough_ok"] or probe["telemetry_source"]),
            "platform": sys.platform,
            "elevated": elevated,
            "elevation_hint": None if elevated else elevation_hint(),
            "needs_admin": needs_admin,
            "degraded": degraded_reason is not None,
            "degraded_reason": degraded_reason,
            "auto_mock_fallback": False,
            "mock_env_pin": env_mock_enabled(),
            "fixture_dir": str(fixture_dir),
            "fixtures_synthesized": synthesized,
            "device_count": 1 if mock else len(_inventory()),
            "fields": {
                "without_admin": list(FIELDS_WITHOUT_ADMIN),
                "requires_admin": list(FIELDS_REQUIRING_ADMIN),
            },
            "actions": [
                {"id": "sentinel.smart", "mutates": False, "needs_admin": True},
                {"id": "sentinel.identify", "mutates": False, "needs_admin": True},
                {"id": "sentinel.errors", "mutates": False, "needs_admin": True},
                {"id": "sentinel.snapshot", "mutates": False, "needs_admin": False},
                {"id": "sentinel.bench", "mutates": False, "needs_admin": False},
                {"id": "observe.trace", "mutates": False, "needs_admin": True},
                {"id": "sentinel.cache_clean", "mutates": True, "needs_admin": False},
            ],
            "not_implemented": [
                "firmware update",
                "secure erase",
                "over-provisioning",
                "NVMe cache flush admin command",
            ],
            "not_implemented_reason": (
                "destructive-class writes; nvme_sentinel is documented read-only"
            ),
        }
    )
    return payload


def _telemetry_source(caps: list[str]) -> TelemetrySource:
    """Map adapter capability strings to the telemetry-source label."""
    if "mock" in caps:
        return TelemetrySource.MOCK
    if "ioctl" in caps:
        return TelemetrySource.IOCTL
    if "device-io-control" in caps:
        return TelemetrySource.DEVICE_IO_CONTROL
    if "host-proxy" in caps:
        return TelemetrySource.HOST_PROXY
    return TelemetrySource.NATIVE_NVME


# --- SMART / identify / errors ----------------------------------------------


def smart_payload(*, device: str | None = None, mock: bool = False) -> dict[str, Any]:
    """Read SMART log page 0x02, falling back to WMI counters when blocked."""
    device_path = device or default_device_path(mock=mock)
    if device_path is None:
        return _no_device_payload(mock=mock)

    payload = _base(mock=mock, device_path=device_path)
    try:
        with open_device(device_path, mock=mock) as dev:
            smart = get_smart_health(dev)
            caps = sorted(dev.capabilities())
        payload.update(
            {
                "ok": True,
                "telemetry_source": _telemetry_source(caps).value,
                "adapter_capabilities": caps,
                "smart": smart.to_dict(),
                "wmi_counters": None,
                "degraded": mock,
                "degraded_reason": (
                    "fixture SMART log — not your drive's wear or temperature" if mock else None
                ),
            }
        )
        return payload
    except (NvmeSentinelError, OSError) as exc:
        failure = classify_failure(exc, device_path)

    # nvme_sentinel.telemetry.read.read_smart puts its WMI rescue *inside* the
    # `with get_adapter(...)` body, so a PermissionDenied raised by open() escapes
    # it. Non-elevated Windows always fails at open(), which is exactly the case
    # the fallback exists for — so run the fallback here instead.
    counters = _wmi_counters(device_path)
    payload.update(
        {
            "ok": counters is not None,
            "telemetry_source": TelemetrySource.WMI.value if counters else None,
            "adapter_capabilities": ["wmi"] if counters else [],
            "smart": None,
            "wmi_counters": counters,
            "degraded": True,
            "needs_admin": failure.needs_admin,
            "degraded_reason": (
                f"adapter fell back to WMI ({failure.reason}) — percentage_used, "
                "available_spare, data_units_* and media_and_data_integrity_errors "
                "are not exposed on this path"
                if counters
                else failure.reason
            ),
        }
    )
    return payload


def identify_payload(*, device: str | None = None, mock: bool = False) -> dict[str, Any]:
    """Identify Controller plus the active namespace list. Admin-only path."""
    device_path = device or default_device_path(mock=mock)
    if device_path is None:
        return _no_device_payload(mock=mock)

    payload = _base(mock=mock, device_path=device_path)
    try:
        with open_device(device_path, mock=mock) as dev:
            ctrl = identify_controller(dev)
            caps = sorted(dev.capabilities())
            try:
                nsids = active_namespace_list(dev)
            except NvmeSentinelError:
                nsids = []
            namespaces: list[dict[str, Any]] = []
            for nsid in nsids[:8]:
                try:
                    namespaces.append(identify_namespace(dev, nsid).model_dump())
                except (NvmeSentinelError, ValueError):
                    continue
        payload.update(
            {
                "ok": True,
                "telemetry_source": _telemetry_source(caps).value,
                "adapter_capabilities": caps,
                "controller": ctrl.model_dump(),
                "namespace_ids": nsids,
                "namespaces": namespaces,
                "degraded": mock,
                "degraded_reason": "fixture Identify payload — not your controller" if mock else None,
            }
        )
        return payload
    except (NvmeSentinelError, OSError) as exc:
        failure = classify_failure(exc, device_path)

    payload.update(
        {
            "ok": False,
            "telemetry_source": None,
            "controller": None,
            "namespace_ids": [],
            "namespaces": [],
            "degraded": True,
            "needs_admin": failure.needs_admin,
            "degraded_reason": (
                f"{failure.reason} — Identify Controller has no WMI equivalent, "
                "so model/serial/firmware here come from the OS inventory instead"
            ),
            "inventory_fallback": _inventory_row(device_path),
        }
    )
    return payload


def _inventory_row(device_path: str) -> dict[str, Any] | None:
    for dev in _inventory():
        if dev.device_path == device_path:
            return dict(dev.model_dump())
    return None


def errors_payload(
    *,
    device: str | None = None,
    mock: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Read the NVMe Error Information log (page 0x01). Admin-only path."""
    device_path = device or default_device_path(mock=mock)
    if device_path is None:
        return _no_device_payload(mock=mock)

    payload = _base(mock=mock, device_path=device_path)
    try:
        with open_device(device_path, mock=mock) as dev:
            caps = sorted(dev.capabilities())
            # SMART carries the entry count; without it we would guess at the
            # transfer size and either truncate the log or over-read it.
            reported = 0
            try:
                reported = get_smart_health(dev).number_of_error_information_log_entries
            except NvmeSentinelError:
                reported = 0
            want = limit if limit is not None else reported
            want = max(1, min(want or 1, _MAX_ERROR_ENTRIES))
            entries = get_error_info_log(dev, want)
        recorded = [e.model_dump() for e in entries if e.error_count > 0]
        payload.update(
            {
                "ok": True,
                "telemetry_source": _telemetry_source(caps).value,
                "adapter_capabilities": caps,
                "reported_entry_count": reported,
                "requested_entries": want,
                "entries": recorded,
                "empty_slots": len(entries) - len(recorded),
                "degraded": mock,
                "degraded_reason": "fixture error log — always empty" if mock else None,
            }
        )
        return payload
    except (NvmeSentinelError, OSError) as exc:
        failure = classify_failure(exc, device_path)

    counters = _wmi_counters(device_path)
    payload.update(
        {
            "ok": False,
            "telemetry_source": None,
            "entries": [],
            "reported_entry_count": None,
            "degraded": True,
            "needs_admin": failure.needs_admin,
            "degraded_reason": (
                f"{failure.reason} — the NVMe error log is passthrough-only; WMI's "
                "ReadErrorsTotal/WriteErrorsTotal are the closest non-admin substitute"
            ),
            "wmi_counters": counters,
        }
    )
    return payload


# --- snapshot ---------------------------------------------------------------


def build_snapshot(device_path: str, *, mock: bool) -> tuple[DeviceSnapshot, str | None]:
    """Assemble a DeviceSnapshot, returning (snapshot, degraded_reason).

    nvme_sentinel.snapshot.collect.collect_snapshot is not reused: it routes the
    mock path through ``get_adapter(force="mock")``, whose fixture directory is
    cwd-relative, so it only works when the server was started from the repo root.
    """
    import base64

    from nvme_sentinel.hal.enums import AdminOpcode, CNSValue, LogPageID
    from nvme_sentinel.hal.interface import AdminCommand

    caps: list[str] = []
    identify_dict: dict[str, int | str] | None = None
    device_info: dict[str, str | int | bool] | None = None
    smart_dict: dict[str, int | str] | None = None
    identify_b64: str | None = None
    smart_b64: str | None = None
    source = TelemetrySource.MOCK if mock else TelemetrySource.NATIVE_NVME
    degraded_reason: str | None = (
        "fixture snapshot — safe to share, but it describes no real drive" if mock else None
    )

    try:
        with open_device(device_path, mock=mock) as dev:
            caps = sorted(dev.capabilities())
            source = _telemetry_source(caps)
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
            device_info = {
                "path": info.path,
                "model": info.model,
                "serial": info.serial,
                "firmware_rev": info.firmware_rev,
                "namespace_count": info.namespace_count,
                "is_nvme": info.is_nvme,
            }
            smart_dict = get_smart_health(dev).to_dict()

            # Raw payloads let a snapshot be replayed through the host-proxy adapter.
            id_res = dev.admin_passthru(
                AdminCommand(
                    opcode=int(AdminOpcode.IDENTIFY),
                    cdw10=int(CNSValue.IDENTIFY_CONTROLLER),
                    data_len=4096,
                )
            )
            if id_res.status == 0 and len(id_res.data) >= 4096:
                identify_b64 = base64.standard_b64encode(id_res.data[:4096]).decode("ascii")
            sm_res = dev.admin_passthru(
                AdminCommand(
                    opcode=int(AdminOpcode.GET_LOG_PAGE),
                    nsid=0xFFFFFFFF,
                    cdw10=int(LogPageID.SMART_HEALTH) | (127 << 16),
                    data_len=512,
                )
            )
            if sm_res.status == 0 and len(sm_res.data) >= 512:
                smart_b64 = base64.standard_b64encode(sm_res.data[:512]).decode("ascii")
        wmi_counters = None
    except (NvmeSentinelError, OSError) as exc:
        failure = classify_failure(exc, device_path)
        wmi_counters = _wmi_counters(device_path)
        source = TelemetrySource.WMI if wmi_counters else TelemetrySource.USB_BRIDGE_DEGRADED
        degraded_reason = (
            f"adapter fell back to WMI ({failure.reason})"
            if wmi_counters
            else failure.reason
        )

    snapshot = DeviceSnapshot(
        collected_at=datetime.now(timezone.utc),
        device_path=device_path,
        platform=sys.platform,
        telemetry_source=source,
        readonly=True,
        adapter_capabilities=caps,
        device_info=device_info,
        identify_controller=identify_dict,
        smart_health=smart_dict,
        wmi_fallback=wmi_counters,
        identify_controller_b64=identify_b64,
        smart_health_b64=smart_b64,
        notes=degraded_reason,
    )
    return snapshot, degraded_reason


def snapshot_payload(
    *,
    device: str | None = None,
    mock: bool = False,
    save: bool = True,
) -> dict[str, Any]:
    """Collect a read-only snapshot and optionally persist it under OPENVAULT_HOME."""
    device_path = device or default_device_path(mock=mock)
    if device_path is None:
        return _no_device_payload(mock=mock)

    snapshot, degraded_reason = build_snapshot(device_path, mock=mock)
    payload = _base(mock=mock, device_path=device_path)
    saved_to: str | None = None
    if save:
        from openmw.openvault.paths import ensure_home

        out_dir = ensure_home() / "snapshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = snapshot.collected_at.strftime("%Y%m%dT%H%M%SZ")
        safe = "".join(ch if ch.isalnum() else "-" for ch in device_path).strip("-")
        out = out_dir / f"{stamp}-{safe or 'device'}.json"
        out.write_bytes(snapshot.model_dump_json(indent=2).encode("utf-8"))
        saved_to = str(out)

    payload.update(
        {
            "ok": snapshot.smart_health is not None or snapshot.wmi_fallback is not None,
            "telemetry_source": snapshot.telemetry_source.value,
            "telemetry_source_label": snapshot.telemetry_source.label(),
            "degraded": degraded_reason is not None,
            "degraded_reason": degraded_reason,
            "saved_to": saved_to,
            "snapshot": snapshot.model_dump(mode="json"),
        }
    )
    return payload


# --- bench ------------------------------------------------------------------


def bench_payload(
    *,
    device: str | None = None,
    mock: bool = False,
    profile: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Paired-snapshot wear delta; an fio/diskspd stress leg only on explicit confirm."""
    import shutil

    from nvme_sentinel.bench.run import build_bench_run_report
    from nvme_sentinel.stress.parser import StressResult
    from nvme_sentinel.stress.profiles import STANDARD_PROFILES

    device_path = device or default_device_path(mock=mock)
    if device_path is None:
        return _no_device_payload(mock=mock)

    payload = _base(mock=mock, device_path=device_path)
    tools = {
        "fio": shutil.which("fio") is not None,
        "diskspd": shutil.which("diskspd") is not None,
    }
    profiles = [p.name for p in STANDARD_PROFILES]

    stress_result: StressResult | None = None
    stress_note: str | None = None
    if profile is None:
        stress_note = (
            "no stress profile requested — this run is the read-only wear delta "
            "between two SMART snapshots"
        )
    elif profile not in profiles:
        stress_note = f"unknown profile {profile!r}; available: {', '.join(profiles)}"
    elif not confirm:
        # A stress leg writes to the drive and consumes endurance. It never runs
        # implicitly, matching the cache-clean rule.
        stress_note = (
            f"profile {profile!r} not run: stress writes to the drive and burns "
            "endurance, so it requires confirm=true"
        )
    elif mock:
        stress_note = "stress profiles are not simulated — mock runs stay read-only"
    else:  # pragma: no cover - requires fio/diskspd on PATH
        stress_result, stress_note = _run_stress(device_path, profile, tools)

    before, reason_before = build_snapshot(device_path, mock=mock)
    after, reason_after = build_snapshot(device_path, mock=mock)
    report = build_bench_run_report(
        device_path,
        before,
        after,
        stress_result=stress_result,
        enclosure_class="internal" if not mock else "mock",
    )
    degraded_reason = reason_before or reason_after
    payload.update(
        {
            "ok": before.smart_health is not None,
            "degraded": degraded_reason is not None,
            "degraded_reason": degraded_reason,
            "telemetry_source": before.telemetry_source.value,
            "tools": tools,
            "profiles": profiles,
            "stress_profile": profile,
            "stress_note": stress_note,
            "wear_delta": report.wear_delta.model_dump(),
            "env_manifest": report.env_manifest.model_dump(mode="json"),
            "stress_result": None if stress_result is None else _stress_dict(stress_result),
        }
    )
    return payload


def _stress_dict(result: Any) -> dict[str, Any]:
    from dataclasses import asdict

    data = dict(asdict(result))
    data.pop("raw", None)  # tool-specific blob; too large for a console payload
    return data


def _run_stress(
    device_path: str,
    profile: str,
    tools: dict[str, bool],
) -> tuple[Any, str]:  # pragma: no cover - requires fio/diskspd on PATH
    """Run one stress profile with whichever tool is installed."""
    from nvme_sentinel.stress.diskspd import DiskspdRunner
    from nvme_sentinel.stress.fio import FioRunner
    from nvme_sentinel.stress.profiles import STANDARD_PROFILES

    from openmw.openvault.paths import ensure_home

    job = next(p for p in STANDARD_PROFILES if p.name == profile)
    out_dir = ensure_home() / "bench"
    try:
        if tools["fio"]:
            return FioRunner().run(device_path, job, out_dir), f"fio ran profile {profile!r}"
        if tools["diskspd"]:
            return (
                DiskspdRunner().run(device_path, job, out_dir),
                f"diskspd ran profile {profile!r}",
            )
    except (NvmeSentinelError, OSError, ValueError, subprocess.SubprocessError) as exc:
        return None, f"stress run failed: {exc}"
    return None, "neither fio nor diskspd is on PATH — install one to run stress profiles"
