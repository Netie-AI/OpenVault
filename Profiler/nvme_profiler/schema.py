"""Capability probe and path-trace schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AccelerationTier(str, Enum):
    """Kernel-level acceleration paths the host may use."""

    BASELINE = "baseline"
    IO_URING_NVME = "io_uring_nvme"
    GDS = "gds"
    SPDK = "spdk"
    WINDOWS_IORING = "windows_ioring"


class GpuVendor(str, Enum):
    """Discrete GPU vendor detected on the host."""

    NONE = "none"
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    UNKNOWN = "unknown"


class GpuClass(str, Enum):
    """GPU product class for GDS / acceleration eligibility."""

    NONE = "none"
    CONSUMER = "consumer"
    WORKSTATION = "workstation"
    UNKNOWN = "unknown"


class HopId(str, Enum):
    """Data-path hop identifiers (VISION.md crown jewel)."""

    SSD_ADMIN = "ssd_admin"
    DRIVER_IOCTL = "driver_ioctl"
    PCIE_LINK = "pcie_link"
    CPU_COPY = "cpu_copy"
    HOST_RAM = "host_ram"
    RAM_TO_VRAM = "ram_to_vram"
    GPU_COMPUTE = "gpu_compute"


class NvmeDeviceProbe(BaseModel):
    """One NVMe or storage device from capability probe."""

    model_config = ConfigDict(frozen=True)

    device_path: str
    friendly_name: str = ""
    is_boot_drive: bool = False
    is_nvme: bool = False
    bus_type: str = ""
    telemetry_source_hint: str = "unknown"
    has_spare_candidate: bool = False


class CapabilityManifest(BaseModel):
    """Host capability manifest for acceleration tier selection."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    os: str
    kernel: str | None = None
    python_version: str
    gpu_vendor: GpuVendor = GpuVendor.NONE
    gpu_class: GpuClass = GpuClass.NONE
    gpu_model: str | None = None
    cuda_version: str | None = None
    rocm_version: str | None = None
    nvme_devices: list[NvmeDeviceProbe] = Field(default_factory=list)
    enabled_tiers: list[AccelerationTier] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)


class HopRecord(BaseModel):
    """Single hop timing in a path trace."""

    model_config = ConfigDict(frozen=True)

    hop_id: HopId
    start_ts: float
    end_ts: float
    bytes_moved: int = 0
    duration_ms: float
    notes: str = ""


class PathTraceEnvManifest(BaseModel):
    """Extended environment manifest for path trace reports."""

    model_config = ConfigDict(frozen=True)

    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    platform: str
    python_version: str
    kernel: str | None = None
    device_path: str = ""
    nsys_version: str | None = None
    capability_manifest: CapabilityManifest | None = None


class PathTraceReport(BaseModel):
    """Full-path bottleneck trace report (parallel to BenchRunReport)."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    env_manifest: PathTraceEnvManifest
    hop_timeline: list[HopRecord] = Field(default_factory=list)
    bottleneck_hop: HopId | None = None
    gpu_idle_pct_waiting_on_io: float | None = None
    html_path: str | None = None
