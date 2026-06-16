# nvme-sentinel — Long-Term Vision (post-P6)

> **Not in `task.md` runbook.** Capture ambition here; execute only after P6 `BenchRunReport`
> ships. This document does not change near-term scope.

---

## Thesis

A **hardware-aware observability layer for AI workloads** that answers:

1. Where did data go (SSD → DRAM → PCIe → GPU)?
2. Where is the bottleneck?
3. What did the workload cost in SSD wear and thermals?

Open source wins by **adoption through trust**: reproducible wear accounting anyone can verify
on commodity hardware — not a price tag.

---

## What to build (later)

| Capability | Approach | Priority |
|------------|----------|----------|
| SSD wear / SMART | **Core** — nvme-sentinel today | Shipped |
| PCIe link health | `lspci` link width/speed, AER counters | High — underserved |
| Data-path hop timing | Trace batch SSD→page cache→DRAM→PCIe→GPU | Crown jewel |
| GPU utilization | Wrap NVML / `nvidia-smi` — do not reimplement | Integrate |
| CPU / RAM | OS perf counters, ECC where available | Integrate |
| Training-step overlay | Hardware telemetry correlated with steps — not a TensorBoard clone | Integrate |
| Inference KV-offload measurement | LMCache + vLLM disk backend + `BenchRunReport` | Near-term after P6 |
| Training weight tiering study | Compare aiDAPTIV-style claims with evidence | **Later research track** |
| One-click Docker / PS1 install | Packaging after engine is real | Last |

---

## aiDAPTIV positioning (evidence layer)

Phison aiDAPTIV+ is proprietary **host middleware + high-endurance cache SSDs**. We do not
clone or improve their controller stack.

nvme-sentinel is the **instrument**: prove where tiering helps ("GPU idle 40% waiting on SSD
reads → prefetch/tiering earns its keep"). Vendor-neutral, reproducible, more valuable to
buyers than any single middleware vendor's marketing chart.

---

## NVMeVirt lab (storage research)

For FTL/scheduling experiments without risking real NAND, use [NVMeVirt](https://github.com/snu-csl/nvmevirt)
on Linux. nvme-sentinel's `LinuxNvmeAdapter` issues the same admin commands against emulated
devices — HAL design pays off.

---

*This vision extends P6; it does not replace P0–P6 sequencing.*
