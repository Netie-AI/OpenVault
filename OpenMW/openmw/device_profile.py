"""Hardware detection and cached DeviceProfile for model routing."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

import psutil
import structlog

log = structlog.get_logger()

_T = TypeVar("_T")
_SENTINEL = object()

# Hard wall-clock cap on hardware probes that may block indefinitely on native
# Windows ctypes calls (DeviceIoControl has no timeout/overlapped-I/O path —
# see PART 12 hang investigation in MASTER_HANDOFF.md). A daemon thread is used
# deliberately: ThreadPoolExecutor registers an atexit handler that *joins*
# worker threads even after a caller-side timeout fires, which re-introduces
# the hang at process exit. A plain daemon thread leaks silently instead.
_HARDWARE_PROBE_TIMEOUT_S = 5.0

# Benchmark has its own internal 5s deadline loop (_BENCHMARK_DURATION_S below) but
# that deadline check only fires *between* reads - a single blocked read/write/tempfile
# call on a stalled drive can still hang past it. This wrapper timeout needs headroom
# above the benchmark's own intended duration so a slow-but-honest run isn't killed early.
_BENCHMARK_TIMEOUT_S = 15.0


def _with_timeout(
    fn: Callable[..., _T],
    *args: object,
    timeout_s: float = _HARDWARE_PROBE_TIMEOUT_S,
    default: _T,
    **kwargs: object,
) -> _T:
    """Run fn with a hard wall-clock timeout; return default on timeout or error.

    Does not (and cannot) kill a hung native call — if fn blocks forever in a
    ctypes/DeviceIoControl call, the daemon thread leaks until process exit.
    What this guarantees is that the *caller* is never blocked past timeout_s.
    """
    box: list[object] = [_SENTINEL]

    def _runner() -> None:
        try:
            box[0] = fn(*args, **kwargs)
        except Exception as exc:
            log.debug("hardware_probe_failed", fn=getattr(fn, "__name__", repr(fn)), error=str(exc))
            box[0] = default

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if box[0] is _SENTINEL:
        log.warning(
            "hardware_probe_timeout",
            fn=getattr(fn, "__name__", repr(fn)),
            timeout_s=timeout_s,
        )
        return default
    return box[0]  # type: ignore[return-value]


_DEFAULT_CACHE_DIR = Path.home() / ".openmw"
_DEFAULT_CACHE_FILE = _DEFAULT_CACHE_DIR / "device_profile.json"

# Reference memory bandwidth (GB/s) for common GPUs — used when NVML lacks the field.
_GPU_BANDWIDTH_GBPS: dict[str, float] = {
    "RTX 4090": 1008.0,
    "RTX 4080": 717.0,
    "RTX 4070": 504.0,
    "RTX 3090": 936.0,
    "RTX 3080": 760.0,
    "RTX 3060": 360.0,
    "A100": 2039.0,
    "H100": 3350.0,
}
_DEFAULT_GPU_BANDWIDTH_GBPS = 100.0
_DEFAULT_CPU_RAM_BANDWIDTH_GBPS = 50.0
_DEFAULT_NVME_SEQ_READ_GBPS = 3.5
_BENCHMARK_DURATION_S = 5.0
_BENCHMARK_CHUNK_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class DeviceProfile:
    """Detected hardware dimensions used for model routing and offload planning."""

    gpu_name: str | None
    gpu_vram_gb: float
    gpu_bandwidth_gbps: float
    system_ram_gb: float
    cpu_cores: int
    nvme_model: str | None
    nvme_seq_read_gbps: float
    nvme_endurance_tbw: float
    unified_memory: bool = False
    cpu_inference_mode: bool = False


@dataclass(frozen=True)
class _CachedProfileEnvelope:
    boot_id: str
    detected_at: str
    profile: DeviceProfile


def default_cache_path() -> Path:
    """Return the default JSON cache path for device profiles."""
    return _DEFAULT_CACHE_FILE


def read_boot_id() -> str:
    """Return a stable identifier for the current boot session."""
    if sys.platform == "linux":
        boot_id_path = Path("/proc/sys/kernel/random/boot_id")
        try:
            return boot_id_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return str(int(psutil.boot_time()))


def _gpu_bandwidth_from_name(gpu_name: str | None) -> float:
    if gpu_name is None:
        return _DEFAULT_CPU_RAM_BANDWIDTH_GBPS
    upper = gpu_name.upper()
    for key, bandwidth in _GPU_BANDWIDTH_GBPS.items():
        if key.upper() in upper:
            return bandwidth
    return _DEFAULT_GPU_BANDWIDTH_GBPS


def _probe_nvidia_gpu() -> tuple[str | None, float]:
    """Query the first NVIDIA GPU via NVML."""
    try:
        import pynvml
    except ImportError:
        return None, 0.0

    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        raw_name = pynvml.nvmlDeviceGetName(handle)
        gpu_name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        vram_gb = float(mem.total) / (1024**3)
        return gpu_name, vram_gb
    except Exception as exc:  # noqa: BLE001 — NVML errors vary by driver state
        log.debug("nvml_probe_failed", error=str(exc))
        return None, 0.0
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:  # noqa: BLE001
            pass


def _run_cmd(argv: list[str], timeout: float = 15.0) -> tuple[int, str]:
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


def _probe_amd_gpu() -> tuple[str | None, float]:
    """Best-effort AMD GPU detection via rocm-smi."""
    rocm_smi = shutil.which("rocm-smi")
    if rocm_smi is None:
        return None, 0.0
    code, text = _run_cmd([rocm_smi, "--showproductname"])
    if code != 0 or not text:
        return None, 0.0
    for line in text.splitlines():
        if "Card series" in line or "GPU" in line:
            parts = line.split(":", maxsplit=1)
            if len(parts) == 2:
                return parts[1].strip(), 0.0
    return None, 0.0


def _probe_apple_silicon() -> tuple[str | None, bool]:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return None, False
    model = platform.processor() or "Apple Silicon"
    if model == "":
        model = "Apple Silicon"
    return model, True


def _probe_system_ram_gb() -> float:
    total = float(psutil.virtual_memory().total)
    return max(total / (1024**3), 1.0)


def _probe_cpu_cores() -> int:
    count = psutil.cpu_count(logical=True)
    return max(int(count or 1), 1)


def _select_primary_nvme() -> tuple[str | None, str | None]:
    """Return (model, device_path) for the boot or first NVMe drive."""
    try:
        from nvme_sentinel.inventory.discovery import list_devices
    except ImportError:
        return None, None

    devices = list_devices()
    nvme_devices = [d for d in devices if d.is_nvme]
    if not nvme_devices:
        return None, None

    boot_candidates = [
        d
        for d in nvme_devices
        if "C" in {x.upper().rstrip(":") for x in d.drive_letters}
        or (sys.platform == "win32" and d.device_path.endswith("PhysicalDrive0"))
    ]
    chosen = boot_candidates[0] if boot_candidates else nvme_devices[0]
    model = chosen.model or chosen.friendly_name or None
    return model, chosen.device_path


def _estimate_endurance_tbw(device_path: str | None) -> float:
    """Estimate rated TBW from SMART percentage_used and data_units_written."""
    if device_path is None:
        return 0.0
    try:
        from nvme_sentinel.telemetry.read import read_smart

        result = read_smart(device_path)
        smart = result.smart
        if smart is None or smart.percentage_used <= 0:
            return 0.0
        bytes_written = smart.data_units_written * 512 * 1000
        rated_bytes = bytes_written / (smart.percentage_used / 100.0)
        return float(rated_bytes / 1e12)
    except Exception as exc:  # noqa: BLE001 — hardware/mock paths vary
        log.debug("endurance_probe_failed", device=device_path, error=str(exc))
        return 0.0


def _resolve_benchmark_path(device_path: str | None) -> Path | None:
    """Pick a filesystem path suitable for a sequential-read micro-benchmark."""
    if device_path is not None and sys.platform == "win32":
        for letter in ("C", "D", "E", "F"):
            root = Path(f"{letter}:\\")
            if root.exists():
                return root
    if sys.platform == "win32":
        return Path("C:\\") if Path("C:\\").exists() else None
    if sys.platform == "linux":
        return Path("/") if Path("/").exists() else None
    if sys.platform == "darwin":
        return Path("/") if Path("/").exists() else None
    return None


def benchmark_nvme_seq_read_gbps(
    *,
    device_path: str | None = None,
    duration_s: float = _BENCHMARK_DURATION_S,
) -> float:
    """Run a short sequential read benchmark; return GB/s (decimal)."""
    base = _resolve_benchmark_path(device_path)
    if base is None:
        return _DEFAULT_NVME_SEQ_READ_GBPS

    total_bytes = 0
    deadline = time.monotonic() + duration_s
    try:
        with tempfile.NamedTemporaryFile(dir=base, delete=True) as tmp:
            tmp.write(b"\0" * min(_BENCHMARK_CHUNK_BYTES, 64 * 1024 * 1024))
            tmp.flush()
            path = Path(tmp.name)
            with path.open("rb") as handle:
                while time.monotonic() < deadline:
                    chunk = handle.read(8 * 1024 * 1024)
                    if not chunk:
                        handle.seek(0)
                        continue
                    total_bytes += len(chunk)
    except OSError as exc:
        log.debug("nvme_benchmark_failed", error=str(exc))
        return _DEFAULT_NVME_SEQ_READ_GBPS

    elapsed = max(duration_s, 1e-6)
    return (total_bytes / elapsed) / 1e9


def _load_cache(cache_path: Path) -> _CachedProfileEnvelope | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        profile_raw = payload.get("profile")
        if not isinstance(profile_raw, dict):
            return None
        profile = DeviceProfile(**profile_raw)
        boot_id = str(payload.get("boot_id", ""))
        detected_at = str(payload.get("detected_at", ""))
        return _CachedProfileEnvelope(boot_id=boot_id, detected_at=detected_at, profile=profile)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        log.debug("device_profile_cache_read_failed", error=str(exc))
        return None


def _save_cache(cache_path: Path, envelope: _CachedProfileEnvelope) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "boot_id": envelope.boot_id,
        "detected_at": envelope.detected_at,
        "profile": asdict(envelope.profile),
    }
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _detect_uncached(
    *,
    benchmark_fn: Callable[..., float] | None = None,
) -> DeviceProfile:
    """Run full hardware detection without reading cache."""
    benchmark = benchmark_fn or benchmark_nvme_seq_read_gbps

    log.info("detect_stage", stage="ram_cores_start")
    system_ram_gb = _probe_system_ram_gb()
    cpu_cores = _probe_cpu_cores()
    apple_name, is_apple = _probe_apple_silicon()
    log.info("detect_stage", stage="ram_cores_done", ram_gb=round(system_ram_gb, 1))

    gpu_name: str | None = None
    gpu_vram_gb = 0.0
    unified_memory = False
    cpu_inference_mode = False

    if is_apple:
        gpu_name = apple_name
        gpu_vram_gb = system_ram_gb
        unified_memory = True
    else:
        log.info("detect_stage", stage="nvidia_gpu_start")
        _no_nvidia_default: tuple[str | None, float] = (None, 0.0)
        nvidia_name, nvidia_vram = _with_timeout(
            _probe_nvidia_gpu,
            timeout_s=_HARDWARE_PROBE_TIMEOUT_S,
            default=_no_nvidia_default,
        )
        log.info("detect_stage", stage="nvidia_gpu_done", found=nvidia_name is not None)
        if nvidia_name is not None:
            gpu_name = nvidia_name
            gpu_vram_gb = nvidia_vram
        else:
            log.info("detect_stage", stage="amd_gpu_start")
            _no_amd_default: tuple[str | None, float] = (None, 0.0)
            amd_name, _ = _with_timeout(
                _probe_amd_gpu,
                timeout_s=_HARDWARE_PROBE_TIMEOUT_S,
                default=_no_amd_default,
            )
            log.info("detect_stage", stage="amd_gpu_done", found=amd_name is not None)
            if amd_name is not None:
                gpu_name = amd_name
                gpu_vram_gb = 0.0
            else:
                cpu_inference_mode = True

    if gpu_name is None and not is_apple:
        cpu_inference_mode = True

    if cpu_inference_mode and not is_apple:
        gpu_bandwidth_gbps = _DEFAULT_CPU_RAM_BANDWIDTH_GBPS
    else:
        gpu_bandwidth_gbps = _gpu_bandwidth_from_name(gpu_name)

    log.info("detect_stage", stage="select_nvme_start")
    _no_nvme_default: tuple[str | None, str | None] = (None, None)
    nvme_model, nvme_path = _with_timeout(
        _select_primary_nvme,
        timeout_s=_HARDWARE_PROBE_TIMEOUT_S,
        default=_no_nvme_default,
    )
    log.info("detect_stage", stage="select_nvme_done", nvme_model=nvme_model)

    log.info("detect_stage", stage="benchmark_start")
    nvme_seq_read_gbps = _with_timeout(
        benchmark,
        device_path=nvme_path,
        timeout_s=_BENCHMARK_TIMEOUT_S,
        default=_DEFAULT_NVME_SEQ_READ_GBPS,
    )
    log.info("detect_stage", stage="benchmark_done", gbps=round(nvme_seq_read_gbps, 2))

    log.info("detect_stage", stage="endurance_start")
    nvme_endurance_tbw = _with_timeout(
        _estimate_endurance_tbw,
        nvme_path,
        timeout_s=_HARDWARE_PROBE_TIMEOUT_S,
        default=0.0,
    )
    log.info("detect_stage", stage="endurance_done", tbw=round(nvme_endurance_tbw, 1))

    return DeviceProfile(
        gpu_name=gpu_name,
        gpu_vram_gb=round(gpu_vram_gb, 2),
        gpu_bandwidth_gbps=round(gpu_bandwidth_gbps, 1),
        system_ram_gb=round(system_ram_gb, 2),
        cpu_cores=cpu_cores,
        nvme_model=nvme_model,
        nvme_seq_read_gbps=round(nvme_seq_read_gbps, 2),
        nvme_endurance_tbw=round(nvme_endurance_tbw, 1),
        unified_memory=unified_memory,
        cpu_inference_mode=cpu_inference_mode,
    )


def detect(
    *,
    force_refresh: bool = False,
    cache_path: Path | None = None,
    benchmark_fn: Callable[..., float] | None = None,
) -> DeviceProfile:
    """Detect hardware profile, using JSON cache when boot session matches."""
    path = cache_path or default_cache_path()
    boot_id = read_boot_id()

    if not force_refresh:
        cached = _load_cache(path)
        if cached is not None and cached.boot_id == boot_id:
            log.debug("device_profile_cache_hit", boot_id=boot_id)
            return cached.profile

    profile = _detect_uncached(benchmark_fn=benchmark_fn)
    envelope = _CachedProfileEnvelope(
        boot_id=boot_id,
        detected_at=datetime.now(timezone.utc).isoformat(),
        profile=profile,
    )
    _save_cache(path, envelope)
    log.info(
        "device_profile_detected",
        gpu=profile.gpu_name,
        vram_gb=profile.gpu_vram_gb,
        cpu_inference=profile.cpu_inference_mode,
    )
    return profile
