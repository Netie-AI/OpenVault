"""Bench run schema: wear delta + environment manifest."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from nvme_sentinel.snapshot.schema import DeviceSnapshot
from nvme_sentinel.stress.parser import StressResult


class EnvManifest(BaseModel):
    """Reproducibility metadata for a bench run."""

    model_config = ConfigDict(frozen=True)

    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    platform: str
    python_version: str
    kernel: str | None = None
    fio_version: str | None = None
    diskspd_version: str | None = None
    device_path: str
    enclosure_class: str = "unknown"
    stress_tool: str | None = None
    profile_name: str | None = None


class WearDelta(BaseModel):
    """SMART-derived wear deltas between before/after snapshots."""

    model_config = ConfigDict(frozen=True)

    data_units_written_delta: int
    data_units_read_delta: int
    tbw_bytes_estimate: int
    percentage_used_delta: int
    warning_composite_temp_time_minutes_delta: int
    critical_composite_temp_time_minutes_delta: int
    media_and_data_integrity_errors_delta: int


class BenchRunReport(BaseModel):
    """Merged bench artifact: snapshots + stress + wear delta."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    env_manifest: EnvManifest
    snapshot_before: DeviceSnapshot
    snapshot_after: DeviceSnapshot
    stress_result: StressResult | None = None
    wear_delta: WearDelta
    html_path: str | None = None


def _smart_int(snapshot: DeviceSnapshot, key: str) -> int:
    if snapshot.smart_health is None:
        return 0
    val = snapshot.smart_health.get(key)
    if isinstance(val, int):
        return val
    return 0


def compute_wear_delta(before: DeviceSnapshot, after: DeviceSnapshot) -> WearDelta:
    """Compute wear deltas from paired DeviceSnapshot SMART dicts."""
    duw_before = _smart_int(before, "data_units_written")
    duw_after = _smart_int(after, "data_units_written")
    dur_before = _smart_int(before, "data_units_read")
    dur_after = _smart_int(after, "data_units_read")
    duw_delta = duw_after - duw_before
    # NVMe spec: data units are 1000 x 512 B
    tbw_bytes = duw_delta * 512 * 1000
    return WearDelta(
        data_units_written_delta=duw_delta,
        data_units_read_delta=dur_after - dur_before,
        tbw_bytes_estimate=tbw_bytes,
        percentage_used_delta=_smart_int(after, "percentage_used")
        - _smart_int(before, "percentage_used"),
        warning_composite_temp_time_minutes_delta=_smart_int(
            after, "warning_composite_temp_time_minutes"
        )
        - _smart_int(before, "warning_composite_temp_time_minutes"),
        critical_composite_temp_time_minutes_delta=_smart_int(
            after, "critical_composite_temp_time_minutes"
        )
        - _smart_int(before, "critical_composite_temp_time_minutes"),
        media_and_data_integrity_errors_delta=_smart_int(after, "media_and_data_integrity_errors")
        - _smart_int(before, "media_and_data_integrity_errors"),
    )


def _tool_version(binary: str) -> str | None:
    path = shutil.which(binary)
    if path is None:
        return None
    try:
        proc = subprocess.run(
            [path, "--version"] if binary == "fio" else [path, "-?"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        text = (proc.stdout or proc.stderr).decode(errors="replace").strip()
        return text.splitlines()[0] if text else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def build_env_manifest(
    device_path: str,
    *,
    enclosure_class: str = "unknown",
    stress_tool: str | None = None,
    profile_name: str | None = None,
) -> EnvManifest:
    """Collect environment manifest for reproducible bench reports."""
    kernel: str | None = None
    if sys.platform == "linux":
        kernel = platform.release()
    return EnvManifest(
        platform=sys.platform,
        python_version=sys.version.split()[0],
        kernel=kernel,
        fio_version=_tool_version("fio"),
        diskspd_version=_tool_version("diskspd"),
        device_path=device_path,
        enclosure_class=enclosure_class,
        stress_tool=stress_tool,
        profile_name=profile_name,
    )
