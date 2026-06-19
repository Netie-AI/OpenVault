"""Host capability probe: OS, GPU, NVMe, acceleration tiers."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog
from nvme_sentinel.inventory.discovery import list_devices

from nvme_profiler.schema import (
    AccelerationTier,
    CapabilityManifest,
    GpuClass,
    GpuVendor,
    NvmeDeviceProbe,
)

log = structlog.get_logger()

_WORKSTATION_GPU_PATTERNS = re.compile(
    r"(quadro|tesla|a100|a800|h100|h200|l40|l4|rtx\s*(pro|a)|datacenter|workstation)",
    re.IGNORECASE,
)
_CONSUMER_GPU_PATTERNS = re.compile(
    r"(geforce|gtx|rtx\s*\d|mx\d|iris|radeon\s*rx|arc\s*a)",
    re.IGNORECASE,
)
_IO_URING_MIN_KERNEL = (5, 19)
_WINDOWS_IORING_MIN_BUILD = 17763  # Windows 10 1809


def _run_cmd(argv: list[str], timeout: float = 15.0) -> tuple[int, str]:
    """Run subprocess; return exit code and decoded stdout+stderr."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        text = (proc.stdout or proc.stderr).decode(errors="replace").strip()
        return proc.returncode, text
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


def _parse_kernel_version(release: str) -> tuple[int, int, int]:
    """Parse Linux kernel release string into (major, minor, patch)."""
    match = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", release)
    if match is None:
        return (0, 0, 0)
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3) or 0)
    return (major, minor, patch)


def _probe_nvme_devices() -> list[NvmeDeviceProbe]:
    """Enumerate storage devices via nvme-sentinel inventory."""
    probes: list[NvmeDeviceProbe] = []
    for dev in list_devices():
        telemetry_hint = dev.suggested_telemetry[0] if dev.suggested_telemetry else "unknown"
        probe = NvmeDeviceProbe(
            device_path=dev.device_path,
            friendly_name=dev.friendly_name,
            is_nvme=dev.is_nvme,
            bus_type=dev.bus_type,
            telemetry_source_hint=telemetry_hint,
            is_boot_drive="C" in {x.upper().rstrip(":") for x in dev.drive_letters}
            or (sys.platform == "win32" and dev.device_path.endswith("PhysicalDrive0")),
        )
        probes.append(probe)
    return probes


def _probe_gpu() -> tuple[GpuVendor, GpuClass, str | None, str | None]:
    """Detect GPU vendor, class, model name, and CUDA version."""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return GpuVendor.NONE, GpuClass.NONE, None, None

    code, text = _run_cmd([nvidia_smi, "--query-gpu=name,driver_version", "--format=csv,noheader"])
    if code != 0 or not text:
        return GpuVendor.UNKNOWN, GpuClass.UNKNOWN, None, None

    first_line = text.splitlines()[0]
    parts = [p.strip() for p in first_line.split(",")]
    model = parts[0] if parts else "unknown"

    vendor = GpuVendor.NVIDIA
    if _WORKSTATION_GPU_PATTERNS.search(model):
        gpu_class = GpuClass.WORKSTATION
    elif _CONSUMER_GPU_PATTERNS.search(model):
        gpu_class = GpuClass.CONSUMER
    else:
        gpu_class = GpuClass.UNKNOWN

    cuda_version: str | None = None
    code2, cuda_text = _run_cmd([nvidia_smi])
    if code2 == 0:
        match = re.search(r"CUDA Version:\s*([\d.]+)", cuda_text)
        if match:
            cuda_version = match.group(1)

    return vendor, gpu_class, model, cuda_version


def _probe_rocm_version() -> str | None:
    """Detect ROCm version via rocm-smi if present."""
    rocm_smi = shutil.which("rocm-smi")
    if rocm_smi is None:
        return None
    code, text = _run_cmd([rocm_smi, "--showdriverversion"])
    if code != 0:
        return None
    match = re.search(r"Driver version:\s*(\S+)", text, re.IGNORECASE)
    return match.group(1) if match else text.splitlines()[0] if text else None


def _linux_io_uring_available() -> tuple[bool, str]:
    """Check Linux kernel io_uring support (NVMe uring_cmd needs >= 5.19)."""
    if sys.platform != "linux":
        return False, "io_uring requires Linux"
    release = platform.release()
    major, minor, _ = _parse_kernel_version(release)
    if (major, minor) < _IO_URING_MIN_KERNEL:
        return False, f"kernel {release} < 5.19 required for NVMe io_uring passthrough"
    # Check if io_uring is disabled system-wide
    disabled_path = "/proc/sys/kernel/io_uring_disabled"
    try:
        with open(disabled_path, encoding="utf-8") as fh:
            if fh.read().strip() != "0":
                return False, "io_uring disabled via kernel.io_uring_disabled"
    except OSError:
        pass
    return True, ""


def _gds_available(
    gpu_vendor: GpuVendor,
    gpu_class: GpuClass,
    gpu_model: str | None,
) -> tuple[bool, str]:
    """Probe GPUDirect Storage eligibility (workstation GPU + cuFile)."""
    if sys.platform != "linux":
        return False, "GDS requires Linux"
    if gpu_vendor != GpuVendor.NVIDIA:
        return False, "GDS requires NVIDIA GPU"
    if gpu_class == GpuClass.CONSUMER:
        model = gpu_model or "unknown"
        return (
            False,
            f"GDS not supported on consumer GeForce GPUs ({model}); "
            "cuFile returns unsupported device",
        )
    cufile = shutil.which("cufile") or shutil.which("gdscheck")
    if cufile is None:
        return False, "cuFile/gdscheck not found; GDS driver stack not installed"
    code, text = _run_cmd([cufile, "-h"] if cufile.endswith("cufile") else [cufile])
    if code != 0 and "unsupported" in text.lower():
        return False, f"cuFile probe failed: {text[:200]}"
    return (
        gpu_class == GpuClass.WORKSTATION,
        "GDS requires workstation-class NVIDIA GPU with cuFile",
    )


def _spdk_available(nvme_devices: list[NvmeDeviceProbe]) -> tuple[bool, str]:
    """SPDK needs a spare non-boot NVMe (unbinds kernel driver)."""
    spare = [d for d in nvme_devices if d.is_nvme and not d.is_boot_drive]
    if not spare:
        boot_only = any(d.is_nvme for d in nvme_devices)
        if boot_only:
            return False, "SPDK requires spare non-boot NVMe; boot drive cannot be unbound"
        return False, "no NVMe devices found for SPDK candidate"
    if sys.platform == "linux":
        vfio_mod = Path("/sys/module/vfio")
        if not vfio_mod.exists():
            return False, "VFIO not available; SPDK userspace driver path blocked"
    return True, ""


def _windows_ioring_available() -> tuple[bool, str]:
    """Windows IoRing API available on Win10 1809+."""
    if sys.platform != "win32":
        return False, "IoRing is Windows-only"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        ) as key:
            build_str = str(winreg.QueryValueEx(key, "CurrentBuildNumber")[0])
            build = int(build_str)
            if build < _WINDOWS_IORING_MIN_BUILD:
                return False, f"Windows build {build} < 17763 (1809); IoRing unavailable"
            return True, ""
    except OSError as exc:
        return False, f"could not read Windows build: {exc}"


def _resolve_tiers(
    nvme_devices: list[NvmeDeviceProbe],
    gpu_vendor: GpuVendor,
    gpu_class: GpuClass,
    gpu_model: str | None,
) -> tuple[list[AccelerationTier], list[str]]:
    """Compute enabled acceleration tiers and degradation reasons."""
    enabled: list[AccelerationTier] = [AccelerationTier.BASELINE]
    degraded: list[str] = []

    io_uring_ok, io_uring_reason = _linux_io_uring_available()
    if io_uring_ok:
        enabled.append(AccelerationTier.IO_URING_NVME)
    else:
        degraded.append(f"io_uring_nvme: {io_uring_reason}")

    gds_ok, gds_reason = _gds_available(gpu_vendor, gpu_class, gpu_model)
    if gds_ok:
        enabled.append(AccelerationTier.GDS)
    else:
        degraded.append(f"gds: {gds_reason}")

    spdk_ok, spdk_reason = _spdk_available(nvme_devices)
    if spdk_ok:
        enabled.append(AccelerationTier.SPDK)
    else:
        degraded.append(f"spdk: {spdk_reason}")

    ioring_ok, ioring_reason = _windows_ioring_available()
    if ioring_ok:
        enabled.append(AccelerationTier.WINDOWS_IORING)
    else:
        degraded.append(f"windows_ioring: {ioring_reason}")

    # Mark spare candidates on devices
    has_spare = any(d.is_nvme and not d.is_boot_drive for d in nvme_devices)
    if has_spare:
        log.debug("spare_nvme_candidate_found")

    return enabled, degraded


def run_capability_probe() -> CapabilityManifest:
    """Run full host capability probe and return manifest."""
    kernel: str | None = platform.release() if sys.platform == "linux" else None
    nvme_devices = _probe_nvme_devices()
    gpu_vendor, gpu_class, gpu_model, cuda_version = _probe_gpu()
    rocm_version = _probe_rocm_version() if gpu_vendor == GpuVendor.AMD else None

    enabled, degraded = _resolve_tiers(nvme_devices, gpu_vendor, gpu_class, gpu_model)

    manifest = CapabilityManifest(
        collected_at=datetime.now(timezone.utc),
        os=sys.platform,
        kernel=kernel,
        python_version=sys.version.split()[0],
        gpu_vendor=gpu_vendor,
        gpu_class=gpu_class,
        gpu_model=gpu_model,
        cuda_version=cuda_version,
        rocm_version=rocm_version,
        nvme_devices=nvme_devices,
        enabled_tiers=enabled,
        degraded_reasons=degraded,
    )
    log.info(
        "capability_probe_complete",
        enabled_tiers=[t.value for t in enabled],
        degraded_count=len(degraded),
    )
    return manifest
