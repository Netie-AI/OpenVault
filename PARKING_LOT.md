# OpenVault — Parking lot

> Deferred / research backlog. Not sequenced for near-term coding unless promoted into [`STATUS.md`](STATUS.md).

---

## Engineering backlog

| Item | Notes |
|------|-------|
| Netie `user.env` DPAPI wrap | Out of this tree — `EnvLoader` fix lives in `D:\Netie Space`. Unlock: someone picks it up in that repo. |
| SSH VPS executor | Ship lane; after interface automation A1–A4. |
| Native Win32 device enumeration | Replace PowerShell `Get-PhysicalDisk` spawn in discovery; `_windows_native.py` already proves the ctypes pattern |
| `collect --yes` / non-interactive elevate | Skip `Auto-elevate?` for scripted two-collect sequences |
| Timeout architecture polish | Align inner `subprocess.run(timeout=)` with outer probe budget; kill children on timeout (partially addressed; keep watching zombies) |
| Full laptop EC fan write | Control tier returns `capability: false` until a probe proves writable EC |
| Force GPU clocks / power limits | Scaffold only (`gpu.power_hint`); needs workstation-proven path |

---

## Observability / vision (post-P6)

| Capability | Priority |
|------------|----------|
| PCIe link health (`lspci` width/speed, AER) | High |
| Data-path hop timing (SSD → page cache → DRAM → PCIe → GPU) | Crown jewel — Full-Path Profiler |
| GPU util via NVML / `nvidia-smi` wrap | Integrate |
| CPU / RAM / ECC counters | Integrate |
| Training-step ↔ hardware telemetry overlay | Integrate |
| Inference KV-offload measurement (LMCache / vLLM + BenchRunReport) | Near-term research after wear PRE-FLIGHT |
| Training weight-tiering study (ZeRO-Infinity-style evidence vs vendor claims) | Later research |
| One-click Docker / PS1 install with capability degrade | Last |
| NVMeVirt lab for FTL experiments | Optional Linux research |

---

## Profiler / kernel acceleration (from PART 10 draft)

Open questions before more prompts:

1. Repo split — profiler lives in `Profiler/` vs nested under `nvme_sentinel/` (currently separate tree).
2. Workstation GPU for real GDS testing, or GeForce/laptop only (document “unsupported” with evidence).
3. Spare secondary NVMe for SPDK / wear-readable passthrough (not boot drive).
4. Profiler v1 OS priority — Linux (Nsight + io_uring) vs Windows (IoRing/DirectStorage spike).
5. Predictive prefetch bar — config/demo vs novel algorithm.

Honest constraints already decided:

- GDS not available on commodity GeForce laptops.
- SPDK cannot unbind a laptop boot NVMe.
- Realistic levers: Linux `io_uring` passthrough; Windows IoRing/DirectStorage as exploratory spike.
- Capability probe must auto-detect and degrade (same honesty as WMI degraded-telemetry banner).

---

## Hardware / lab notes (not bugs)

- Windows `stornvme.sys` STOPPED → passthrough `ERROR_INVALID_FUNCTION`; WMI fallback is correct behavior.
- USB-bridged NVMe: WMI does not expose NVMe Data Units Written → wear delta zero is honest.
- Boot drive: do not run endurance workloads; WSL2 sharing issues possible.
- Docker on Windows: Linux ioctl works in container, but real `/dev/nvme*` passthrough needs Linux host + spare drive.
