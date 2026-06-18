# PART 1 Research — R-1 Device Detection Libraries

## Question

Which library gives (a) GPU name + VRAM, (b) NVMe model + sequential read speed,
(c) system RAM total — with the fewest platform-conditional branches on
Windows/Linux/macOS?

## Libraries evaluated

| Library | GPU name/VRAM | NVMe model/speed | RAM / CPU | Cross-platform | Notes |
|---------|---------------|------------------|-----------|----------------|-------|
| **psutil** | No | No (disk usage only) | Yes — `virtual_memory()`, `cpu_count()` | Win/Linux/macOS/FreeBSD | Zero-deps core for RAM and CPU cores |
| **pynvml** (`nvidia-ml-py`) | Yes — NVML bindings | No | No | Win/Linux (NVIDIA driver required) | Direct API; no subprocess parsing; `nvmlDeviceGetName`, `nvmlDeviceGetMemoryInfo` |
| **GPUtil** | Yes (via nvidia-smi) | No | No | NVIDIA only | Thin subprocess wrapper; adds dep without benefit over pynvml |
| **py-cpuinfo** | No | No | CPU brand/features | Win/Linux/macOS | Optional; psutil + `platform.processor()` sufficient for core count |
| **py-nvme** | No | Userspace NVMe benchmark | No | Linux-focused | Low-level passthrough; overlaps nvme-sentinel mission; not needed for model name |
| **nvme-sentinel** (existing dep) | No | Model via inventory + SMART endurance signals | No | Win/Linux | Already in OpenMW; `list_devices()` + adapter SMART for TBW estimate |

## Recommendation for OpenMW PART 1

**Stack:** `psutil` + `nvidia-ml-py` (imported as `pynvml`) + `nvme-sentinel` inventory/SMART.

**Why:**

1. **psutil** — single API for RAM and CPU on all three OSes; no shell parsing.
2. **pynvml** — programmatic NVIDIA VRAM/name without spawning `nvidia-smi`; matches WebUI live-meter plan (PART 6).
3. **nvme-sentinel** — already a dependency; provides NVMe model from OS inventory and SMART `percentage_used` / `data_units_written` for endurance estimation. Avoids duplicating ioctl/IOCTL paths.
4. **Skip GPUtil** — redundant with pynvml.
5. **Skip py-nvme** — nvme-sentinel covers identification; seq-read uses cached micro-benchmark fallback.
6. **Skip py-cpuinfo** — not required for the six PART 1 dimensions (core count only).

## Platform-specific branches (minimal)

| Path | Detection | Fallback |
|------|-----------|----------|
| NVIDIA | `pynvml` init → device 0 name + total VRAM | `cpu_inference_mode=True`, VRAM=0 |
| Apple Silicon | `sys.platform == darwin` + `platform.machine() == arm64` | unified memory: `gpu_vram_gb = system_ram_gb` |
| AMD | `rocm-smi` subprocess (same pattern as nvme-profiler) | treat as CPU inference if no VRAM parsed |
| CPU-only | no GPU probe success | `cpu_inference_mode=True`, bandwidth = RAM estimate |
| NVMe model | `nvme_sentinel.inventory.list_devices()` — prefer boot NVMe | `nvme_model=None`, speed from benchmark or 0 |
| NVMe seq read | cached 5 s temp-file read on boot NVMe path | default 3.5 GB/s (Gen3 conservative) |
| Endurance TBW | derive from SMART when `percentage_used > 0` | `0.0` if unavailable |

## Appendix — PART 1 PRE-FLIGHT decisions

### 1. Library stack

`psutil` + `nvidia-ml-py` + `nvme-sentinel`. Smallest install footprint for cross-platform RAM/CPU; NVML for NVIDIA; existing HAL for NVMe identity and wear.

### 2. Six hardware dimensions + fallbacks

| Dimension | Source | Fallback |
|-----------|--------|----------|
| `gpu_name` | pynvml / rocm-smi / Apple `Apple M*` | `None`; `cpu_inference_mode=True` |
| `gpu_vram_gb` | pynvml mem total / unified RAM on Apple | `0.0` |
| `gpu_bandwidth_gbps` | Lookup table by GPU name | CPU path: `50.0` (DDR5 estimate) |
| `system_ram_gb` | psutil | `8.0` minimum clamp |
| `cpu_cores` | psutil logical count | `1` |
| `nvme_model` | nvme-sentinel inventory | `None` |
| `nvme_seq_read_gbps` | Cached micro-benchmark | `3.5` Gen3 default |
| `nvme_endurance_tbw` | SMART-derived rated TBW | `0.0` |

### 3. Cache strategy

JSON at `~/.openmw/device_profile.json` with `boot_id` + `profile` payload.
Re-use cache when `boot_id` matches current boot session (`/proc/sys/kernel/random/boot_id` on Linux,
`psutil.boot_time()` epoch on Windows/macOS). NVMe seq-read benchmark result stored in cache;
not re-run until next boot.

### 4. Key design decisions

- **Apple Silicon:** `unified_memory=True`, `gpu_vram_gb == system_ram_gb`.
- **AMD:** `rocm-smi` for name; bandwidth table fallback.
- **CPU-only:** `cpu_inference_mode=True`, `gpu_bandwidth_gbps` from RAM bandwidth estimate.
- **NVMe benchmark:** 128 MiB sequential read, 5 s cap; result written to cache once per boot.
