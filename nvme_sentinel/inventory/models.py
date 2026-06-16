"""Inventory models for discovered storage devices."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InventoryDevice(BaseModel):
    """One storage device visible to the host OS."""

    model_config = ConfigDict(frozen=True)

    device_path: str = Field(description="OS path, e.g. \\\\.\\PhysicalDrive0 or /dev/nvme0n1")
    friendly_name: str = ""
    model: str = ""
    serial: str = ""
    size_bytes: int | None = None
    media_type: str = ""
    bus_type: str = ""
    is_nvme: bool = False
    drive_letters: list[str] = Field(default_factory=list)
    linux_nvme_path: str | None = Field(
        default=None,
        description="Linux /dev/nvme* path when mapped from block device",
    )
    suggested_telemetry: list[str] = Field(
        default_factory=list,
        description="Likely telemetry sources: native-nvme, wmi, smartctl, etc.",
    )
