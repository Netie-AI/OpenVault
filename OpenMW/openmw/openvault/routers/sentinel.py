"""NVMe Sentinel telemetry routes.

Mounted by the integrator with ``app.include_router(sentinel_router)``; this file
declares the routes and owns nothing else. Every handler forwards to
``openmw.openvault.sentinel.engine`` (or ``trace`` for admin-command capture)
and returns payloads unchanged so ``source`` and ``degraded_reason`` stay honest.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from openmw.openvault.observe.path import observe_path_payload, timings_path
from openmw.openvault.sentinel.engine import (
    bench_payload,
    capabilities_payload,
    default_device_path,
    devices_payload,
    errors_payload,
    identify_payload,
    smart_payload,
    snapshot_payload,
    use_mock,
)
from openmw.openvault.sentinel.trace import run_admin_trace

router = APIRouter(tags=["sentinel"])


class SnapshotBody(BaseModel):
    device: str | None = None
    mock: bool | None = None


class BenchBody(BaseModel):
    device: str | None = None
    mock: bool | None = None
    kind: str | None = None
    confirm: bool = False


class TraceBody(BaseModel):
    device: str | None = None
    mock: bool | None = None


@router.get("/api/sentinel/devices")
def api_sentinel_devices(
    mock: bool | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    return devices_payload(mock=use_mock(mock), refresh=refresh)


@router.get("/api/sentinel/smart")
def api_sentinel_smart(
    device: str | None = None,
    mock: bool | None = None,
) -> dict[str, Any]:
    return smart_payload(device=device, mock=use_mock(mock))


@router.get("/api/sentinel/identify")
def api_sentinel_identify(
    device: str | None = None,
    mock: bool | None = None,
) -> dict[str, Any]:
    return identify_payload(device=device, mock=use_mock(mock))


@router.get("/api/sentinel/errors")
def api_sentinel_errors(
    device: str | None = None,
    mock: bool | None = None,
) -> dict[str, Any]:
    return errors_payload(device=device, mock=use_mock(mock))


@router.get("/api/sentinel/capabilities")
def api_sentinel_capabilities(
    device: str | None = None,
    mock: bool | None = None,
) -> dict[str, Any]:
    return capabilities_payload(device=device, mock=use_mock(mock))


@router.post("/api/sentinel/snapshot")
def api_sentinel_snapshot(body: SnapshotBody) -> dict[str, Any]:
    return snapshot_payload(device=body.device, mock=use_mock(body.mock))


@router.post("/api/sentinel/bench")
def api_sentinel_bench(body: BenchBody) -> dict[str, Any]:
    return bench_payload(
        device=body.device,
        mock=use_mock(body.mock),
        profile=body.kind,
        confirm=body.confirm,
    )


@router.post("/api/observe/trace")
def api_observe_trace(body: TraceBody) -> dict[str, Any]:
    """Run a read-only admin-command trace, persist timings, return path timeline."""
    mock = use_mock(body.mock)
    device_path = body.device or default_device_path(mock=mock)

    if device_path is None:
        payload: dict[str, Any] = {
            "ok": False,
            "source": "mock" if mock else "live",
            "device": None,
            "degraded": True,
            "degraded_reason": (
                "no storage device detected — Get-PhysicalDisk returned nothing "
                "(pass mock=true to inspect the fixture device instead)"
            ),
            "trace_ok": False,
            "timings_written": False,
        }
        payload.update(observe_path_payload(prefer_live=False))
        return payload

    trace = run_admin_trace(device_path, mock=mock)
    timings_written = False
    if trace.ok and trace.records:
        out = timings_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(trace.records, indent=2), encoding="utf-8")
        timings_written = True

    payload = observe_path_payload(device_path=device_path, prefer_live=True)
    payload["trace_ok"] = trace.ok
    payload["timings_written"] = timings_written
    payload["adapter"] = trace.adapter
    payload["commands"] = trace.commands
    payload["record_count"] = len(trace.records)
    if trace.needs_admin:
        payload["needs_admin"] = True

    if not trace.ok and trace.error:
        payload["degraded"] = True
        prior = payload.get("degraded_reason")
        payload["degraded_reason"] = (
            f"{trace.error}; {prior}" if prior else trace.error
        )

    return payload
