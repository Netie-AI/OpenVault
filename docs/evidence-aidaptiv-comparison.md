# aiDAPTIV vs nvme-sentinel measured evidence (Q5)

> One-page interview narrative. Vendor-reported numbers vs reproducible measurements.

## Positioning

Phison aiDAPTIV+ claims (~10× inference speedup, 120B MoE in 32 GB DRAM) are **vendor-reported**
and hardware-locked to Phison cache SSDs. nvme-sentinel + OpenMW + Profiler wins a different game:
**vendor-neutral proof** of bytes moved, wear cost, and bottleneck hop — not marketing multipliers.

## Side-by-side (claims vs instrument)

| Dimension | aiDAPTIV+ (vendor) | nvme-sentinel stack (measured) |
|-----------|-------------------|--------------------------------|
| KV offload path | Proprietary middleware + Phison SSD | LMCache disk backend + vLLM (OpenMW config) |
| Wear / TBW | Not independently reproducible | `BenchRunReport` from SMART log 0x02 delta |
| Bottleneck | Not published per-hop | `PathTraceReport` — SSD, PCIe, RAM→VRAM, GPU idle % |
| Hardware | Locked SSD + Ubuntu 24.04 / CUDA 13 | Capability probe degrades on GeForce / boot NVMe |
| Prefetch | Proprietary “predictive burst” | Phase-1 naive + Phase-2 heuristic (config-only); profiler compares on/off |

## Upstream lever (not implemented here)

DeepSeek MLA reduces KV bytes per token (architecture change). I/O tiering is **one lever**;
smaller KV upstream reduces every hop — state explicitly in interviews.

## Honest boundaries (same standard as BIWIN WMI finding)

- USB/WMI path: wear delta zero — degraded-telemetry banner, not a bug.
- GeForce laptops: GDS tier disabled — cuFile unsupported on consumer GPUs.
- Boot NVMe: SPDK tier disabled — cannot unbind kernel driver on OS disk.
- Windows IoRing: exploratory only — GDeflate game assets ≠ raw KV tensors.

## Demo artifacts

1. `make probe` → `capability_manifest.json`
2. `make trace-mock` → `path_trace_report.html`
3. `OpenMW/scripts/run_offload_demo.sh` → prefetch on/off comparison JSON

## Interview one-liner

“We don’t clone aiDAPTIV’s firmware. We **measure** whether offload earned its keep: TBW delta,
hop timeline, and GPU idle % waiting on I/O — reproducible on commodity hardware with graceful
degradation when native passthrough isn’t available.”
