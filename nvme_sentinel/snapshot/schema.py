"""JSON schema for read-only device snapshots."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from nvme_sentinel.telemetry.source import TelemetrySource


class DeviceSnapshot(BaseModel):
    """Read-only telemetry bundle exportable to disk or VM shared folder."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    device_path: str
    platform: str
    telemetry_source: TelemetrySource
    readonly: bool = True
    adapter_capabilities: list[str] = Field(default_factory=list)
    device_info: dict[str, str | int | bool] | None = None
    identify_controller: dict[str, int | str] | None = None
    smart_health: dict[str, int | str] | None = None
    wmi_fallback: dict[str, int | str] | None = None
    identify_controller_b64: str | None = Field(
        default=None,
        description="Base64 Identify Controller 4096 B for host-proxy replay",
    )
    smart_health_b64: str | None = Field(
        default=None,
        description="Base64 SMART log 512 B for host-proxy replay",
    )
    notes: str | None = None
