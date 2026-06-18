"""Nsight Systems capture parsing (Linux-first)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import structlog

from nvme_profiler.schema import HopId, HopRecord

log = structlog.get_logger()


def nsys_version() -> str | None:
    """Return nsys version string if installed."""
    nsys = shutil.which("nsys")
    if nsys is None:
        return None
    try:
        proc = subprocess.run(
            [nsys, "--version"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        text = (proc.stdout or proc.stderr).decode(errors="replace").strip()
        return text.splitlines()[0] if text else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def parse_nsys_export_json(export_path: Path) -> list[HopRecord]:
    """Parse nsys stats --report gputrace export JSON into hop records."""
    if not export_path.is_file():
        log.warning("nsys_export_missing", path=str(export_path))
        return []
    try:
        payload = json.loads(export_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("nsys_export_parse_failed", error=str(exc))
        return []
    return _events_to_hops(payload)


def _events_to_hops(payload: object) -> list[HopRecord]:
    """Best-effort mapping of nsys export events to HopRecords."""
    if not isinstance(payload, list):
        return []
    hops: list[HopRecord] = []
    t0 = 0.0
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).lower()
        start_ns = float(item.get("start", 0))
        end_ns = float(item.get("end", start_ns))
        duration_ms = max((end_ns - start_ns) / 1_000_000.0, 0.0)
        hop_id = _classify_nsys_event(name)
        if hop_id is None:
            continue
        start_ts = start_ns / 1_000_000_000.0
        hops.append(
            HopRecord(
                hop_id=hop_id,
                start_ts=start_ts,
                end_ts=start_ts + duration_ms / 1000.0,
                bytes_moved=int(item.get("bytes", 0) or 0),
                duration_ms=duration_ms,
                notes=name,
            )
        )
        t0 = max(t0, start_ts)
    if not hops and isinstance(payload, list) and payload:
        log.debug("nsys_no_classified_events", count=len(payload))
    return hops


def _classify_nsys_event(name: str) -> HopId | None:
    """Map nsys event name substrings to hop IDs."""
    if "memcpy" in name or "htod" in name or "dtoh" in name:
        return HopId.RAM_TO_VRAM
    if "kernel" in name or "cuda" in name or "gpu" in name:
        return HopId.GPU_COMPUTE
    if "copy" in name or "memcpy" in name:
        return HopId.CPU_COPY
    if "pcie" in name:
        return HopId.PCIE_LINK
    return None


def mock_nsys_hops() -> list[HopRecord]:
    """Synthetic nsys-like hops for mock/CI path."""
    return [
        HopRecord(
            hop_id=HopId.PCIE_LINK,
            start_ts=0.01,
            end_ts=0.015,
            bytes_moved=4_194_304,
            duration_ms=5.0,
            notes="mock pcie",
        ),
        HopRecord(
            hop_id=HopId.RAM_TO_VRAM,
            start_ts=0.015,
            end_ts=0.045,
            bytes_moved=67_108_864,
            duration_ms=30.0,
            notes="mock cudaMemcpyAsync H2D",
        ),
        HopRecord(
            hop_id=HopId.GPU_COMPUTE,
            start_ts=0.045,
            end_ts=0.145,
            bytes_moved=0,
            duration_ms=100.0,
            notes="mock attention kernel",
        ),
    ]
