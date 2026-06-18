# PART 10 (DRAFT, pending `confirm`) — Full-Path Bottleneck Profiler & Kernel-Level Acceleration Research

> **Status: draft for review, not yet merged into MASTER_HANDOFF.md.** Per the existing
> HIGH_RISK gate convention, no Cursor/Windsurf prompts should be written from this section
> until PART 10.8 (pre-flight) is resolved.
>
> Verified against the live repo at commit `e3f8789` (tag `v0.1.0` at `320751a`) on 2026-06-16.

---

## 10.0 — Correction to scope: this is not a pivot, it's the build-out of an existing plan

Checked against the repo's own `docs/VISION.md`: the GPU/PCIe/CPU/RAM bottleneck-tracing idea,
the "where did data go" framing, the explicit refusal to clone aiDAPTIV's controller stack, and
the LMCache/vLLM KV-offload measurement track are **already specified there**, and PART 9 of
`MASTER_HANDOFF.md` already drafts the flash-kv-cache exit gate. Nothing below contradicts that.
This document's job is narrower: turn VISION.md's "Data-path hop timing — Crown jewel" bullet
into an actual architecture, and answer the "can we really beat/approach aiDAPTIV with no
firmware access" question with current (2026) prior art instead of assumption.

---

## 10.1 — Repo state (verified via clone + grep, not self-reported)

| Check | Result |
|---|---|
| HEAD | `e3f8789` ("mark v0.1.0 release gate complete"), one commit past the `v0.1.0` tag (`320751a`) — docs-only diff, expected |
| `bench/`, `telemetry/`, `stress/` | All present and non-trivial (schema.py, run.py, report.py; source.py, read.py; fio/diskspd/parser/profiles) |
| GPU/CUDA/vLLM/LMCache/NVML/PCIe references in `nvme_sentinel/` | **Zero matches.** The new direction is a clean slate — no rework debt |
| Test functions | 95 (`grep -c "^def test_"` across `tests/`), consistent with handoff's "~76+" note trending up |
| `pyproject.toml` version | Still `0.0.1` despite the `v0.1.0` git tag — minor inconsistency, bump when convenient, not blocking |

Conclusion: the flash-kv-cache / full-path-profiler direction is **0% started in code, 100%
specified in docs.** That's good news — there's nothing to unwind.

---

## 10.2 — Literature / prior-art map ("a few plans better than aiDAPTIV")

| System | What it actually is | Why it matters to this project | Constraint |
|---|---|---|---|
| **Phison aiDAPTIV+** | Proprietary host middleware, requires Phison's own cache SSD. As of CES/GTC 2026 it's expanded to integrated-GPU laptops; Linux-only (Ubuntu 24.04, kernel 6.14+, CUDA 13). Evicted KV tokens are stored to flash and reused instead of recomputed; vendor claims ~10x inference speedup and lets a 120B MoE model run in 32GB DRAM instead of 96GB. | This is the benchmark to position against. All numbers above are **vendor-reported**, not independently reproducible — that gap is exactly nvme-sentinel's existing "vendor-neutral, reproducible" wedge. | Hardware-locked to their SSD; closed source; can't be cloned (no firmware access, which you already correctly identified) |
| **Mooncake** (Moonshot AI / Kimi) | Open-source, KVCache-centric disaggregated serving — splits prefill/decode, pools CPU/DRAM/SSD of a GPU cluster as a shared KV cache, with a heuristic hotspot-migration scheduler (not full prediction). Most actively maintained system in this space: joined the PyTorch ecosystem (Feb 2026), integrated into vLLM, SGLang, LMCache. | The most mature, most reproducible open baseline to measure against — stronger comparison anchor than aiDAPTIV's marketing numbers. | Designed for clusters; single-node/laptop use is a downscale, not the original target |
| **LMCache** | Open-source multi-tier KV store (GPU → CPU DRAM → local disk/GDS → remote). Disk writes are async and non-blocking relative to the inference thread. Already the component named in VISION.md. | Directly pluggable into vLLM via `kv_connector`; this is the actual middleware layer, not something to rebuild | Best disk-tier performance currently assumes Linux + optional GDS |
| **vLLM native `OffloadingConnector`** (vLLM ≥0.11.0) | Built-in async CPU offload using CUDA DMA, no external dependency | Lighter-weight first step than standing up full LMCache; vendor claims 2–22x TTFT reduction, up to 9x throughput on cache hits | CPU-tier only; no disk backend |
| **DeepSeek Multi-Head Latent Attention (MLA)** | Not an infra trick — a model architecture change. Compresses K/V into a low-rank latent before caching, then reconstructs on read. DeepSeek-V3 reports ~70 KB/token vs 192–328 KB/token for GQA models (2.7–4.7x less data to move in the first place). | Upstream of every I/O-tiering trick below: smaller KV means every later hop (PCIe, NVMe) moves less data. Worth stating explicitly in any write-up so it doesn't read as if I/O tiering is the only lever. | Requires the *model* to be trained/served with MLA — orthogonal to your storage work, not something nvme-sentinel implements |
| **SpeCache / Comet / async-L2-prefetch papers** (2025–2026 arXiv) | Academic, reproducible work on exactly the "predict early, assign ready" mechanism you described: speculatively predict which KV blocks will be attended to before the attention op runs, and prefetch them into GPU L2 cache during otherwise-idle compute windows. One reports 2.15x attention-kernel efficiency on H20 GPUs. | This is the real, citable prior art for "aiDAPTIV-style predictive burst" — public and benchmarkable, unlike Phison's internal numbers | Published as research code, not production middleware; expect integration work |
| **ZeRO-Infinity / ZeRO-Inference** (Microsoft DeepSpeed) | Training/inference-time **weight** tiering across GPU/CPU/NVMe (not KV cache). Explicitly designed around "laptops, desktops, workstations" having terabytes of aggregate CPU+NVMe capacity. | This is the closest existing answer to VISION.md's "training weight tiering study (compare aiDAPTIV-style claims with evidence)" — already the right citation, just confirming it's Microsoft's work rather than Google's | Training-time framing; your near-term scope is inference KV-offload, so this stays "later research track" exactly as VISION.md already says |

---

## 10.3 — The honest hardware-constraint matrix (no firmware access, "all laptops")

This is the part of the user's framing that most needs grounding before any plan is written —
several of the obvious "kernel-level boost" levers simply don't run on commodity laptop hardware.

| Capability | What it requires | Laptop reality | Verdict |
|---|---|---|---|
| **GPUDirect Storage** (true zero-copy NVMe→VRAM DMA, bypasses CPU/RAM bounce buffer entirely) | Tesla/Quadro/RTX PRO-class GPU + `nvidia-fs.ko` + Linux | `cuFile` returns an "unsupported device" error under GeForce drivers — this is a known, industry-wide limitation, not a config issue. Most laptops ship GeForce-class or no discrete GPU. | **Not available on commodity laptops.** Demo-tier only, on workstation-class GPUs if/when available |
| **SPDK** (true kernel-bypass NVMe driver) | Unbinds the device from the kernel entirely (VFIO/UIO) — the device disappears from the OS, no filesystem possible while bound | A laptop's only NVMe is also its boot drive | **Cannot run on a laptop's primary drive at all.** Needs a dedicated spare NVMe — the exact same constraint PART 9's pre-flight already documents for wear-readable testing |
| **io_uring passthrough** (Linux, kernel-mediated, no unbind) | Kernel ≥5.19 for NVMe `uring_cmd` | Works on any Linux laptop NVMe, **including the boot drive**, no special hardware | **The realistic Linux lever.** Async, optionally polled-mode, no IOMMU/VFIO requirement |
| **Windows IoRing / DirectStorage** | Built into Windows 10 1809+ for IoRing, designed for compressed game-asset streaming to consumer GPUs | Works on ordinary consumer hardware — but it's built and tuned for compressed texture tiles via GDeflate, not raw tensor pages. Using it for KV-cache tensors is an open question, not a proven path. | **The realistic Windows lever, but it's a research spike**, not a guaranteed win — must be demoed with the same honesty as the existing WMI degraded-telemetry banner |

The practical takeaway: "boost the speed via kernel-level coding" is real and buildable, but the
specific lever available depends on whether the target machine has a spare drive and/or a
workstation GPU. A one-click installer has to **detect this and degrade gracefully**, not assume
the best-case path everywhere.

---

## 10.4 — Proposed architecture: the "Full-Path Profiler" (VISION.md's "crown jewel")

This operationalizes VISION.md's existing bullet: *"Data-path hop timing — trace batch SSD →
page cache → DRAM → PCIe → GPU."*

**Hops to instrument**, each mapped to a tool rather than hand-rolled where a good one exists
(same philosophy already stated in VISION.md: *"wrap NVML, don't reimplement"*):

1. **SSD-internal latency** — already measured via `_timed()` around the admin passthrough path. Ground truth, already shipped.
2. **Driver/ioctl round trip** — already measured (Linux ioctl / `DeviceIoControl`).
3. **PCIe link** — `lspci` link width/speed + AER counters on Linux; Windows equivalent is genuinely unresearched (flagged in pre-flight below — don't assume parity here).
4. **CPU burst** (page-cache copy, memcpy) — `perf stat` on Linux; ETW on Windows.
5. **Host RAM bandwidth** — simple timestamp deltas around the existing copy path; a STREAM-style microbenchmark for a calibration baseline.
6. **RAM → VRAM transfer** — CUDA event timers around `cudaMemcpyAsync` (or ROCm `hipMemcpy` if AMD is in scope).
7. **GPU compute / idle** — NVML sampling or `nvidia-smi dmon`, correlated against the I/O timeline to compute the actual VISION.md example metric: *"GPU idle 40% waiting on SSD reads."*

**Recommendation:** lean on **NVIDIA Nsight Systems** (`nsys`) as the primary cross-platform
(Linux + Windows) unifying capture tool for hops 4–7 — it already correlates CUDA API calls,
kernel execution, and OS-runtime activity on one timeline — rather than building a bespoke
ETW/perf correlator from scratch. Layer nvme-sentinel's own SSD-side timing (hops 1–2) underneath
it as the storage ground truth Nsight doesn't see.

**New schema**, parallel to the existing `BenchRunReport` pattern:

```
PathTraceReport
├── env_manifest          # reuse existing pattern: OS, kernel, GPU model+driver, CUDA/ROCm ver
├── hop_timeline[]         # per-hop start/end timestamps, bytes moved
├── bottleneck_hop          # computed: highest (wait_time / theoretical_bandwidth) hop
├── gpu_idle_pct_waiting_on_io   # the literal VISION.md metric
└── html_path               # same dark-industrial report convention as BenchRunReport
```

---

## 10.5 — Predictive "early ready assign" research track

Maps directly to SpeCache / Comet / Mooncake's hotspot heuristics, scoped as two phases so the
first is achievable without novel research and the second is the actual differentiator:

- **Phase 1 (build):** naive sequential prefetch — start fetching the next N KV blocks during decode, before they're requested. Cheap, no prediction model needed.
- **Phase 2 (research):** heuristic-driven prefetch using an attention/access-pattern signal, modeled on the published Comet/SpeCache approach, rather than inventing a new predictor from scratch.

Both phases get evaluated against the Full-Path Profiler from 10.4: does prefetching measurably
reduce `gpu_idle_pct_waiting_on_io`? That comparison — not a marketing multiplier — is the
evidence for "our software improves the bottleneck."

---

## 10.6 — One-click setup: the realistic shape

A capability-probe script, extending the existing `Makefile`/`scripts/demo.*` convention, that
detects: OS, GPU vendor/class (consumer vs. workstation), driver/CUDA version, spare-vs-boot NVMe,
and kernel io_uring support — then emits a `capability_manifest.json` deciding which acceleration
tier actually runs, with the same graceful-degradation messaging already established for the WMI
degraded-telemetry banner.

**"All laptops" has to mean "auto-detects and degrades gracefully on all laptops,"** not "runs
GDS/SPDK on all laptops" — that distinction needs to be the literal language used in any demo or
interview narrative to stay consistent with this project's existing honesty pattern (the BIWIN
WMI finding, the degraded-telemetry banner).

---

## 10.7 — Proposed phased plan (≈4–5 months, sequencing tentative pending 10.8)

| Phase | Work | Exit gate |
|---|---|---|
| Q1 | Literature freeze (this doc) + capability-probe script + repo-split decision (10.8.1) | Probe script runs on at least one real laptop, produces a manifest |
| Q2 | Full-Path Profiler v1 — Linux first: Nsight Systems + existing SSD-side `_timed()` fused into `PathTraceReport` | Mock + 1 real machine produce a non-trivial `bottleneck_hop` |
| Q3 | flash-kv-cache MVP per existing PART 9 (LMCache or vLLM native connector + profiler correlation) + Phase-1 naive prefetch | One reproducible run: baseline → offload inference → report, per PART 9's existing exit gate |
| Q4 | Phase-2 predictive prefetch + Windows IoRing/DirectStorage spike (clearly labeled exploratory, not a committed deliverable) + cross-platform parity pass | Prefetch on/off comparison against the profiler, with numbers, not claims |
| Q5 (buffer) | Write-up: side-by-side table of aiDAPTIV's *vendor-reported* numbers vs. your *measured* numbers, interview narrative polish | Same "honest boundary" standard already used for the BIWIN finding |

---

## 10.8 — Pre-flight ambiguities (resolve before any Cursor/Windsurf prompt is written)

1. **Repo split.** PART 9 already says flash-kv-cache should be a separate repo, not bolted onto `nvme-sentinel`. Does the Full-Path Profiler live there too, or as `nvme_sentinel/profiler/`? This changes every prompt that follows.
2. **GPU access.** Is there a workstation-class NVIDIA GPU (RTX PRO / older Quadro/Tesla) available anywhere for real GDS testing, or is all development on laptop integrated/GeForce GPUs? This single fact decides whether 10.3's GDS row is "build it" or "document why we can't, with evidence."
3. **Spare NVMe.** Same open item as PART 9's existing pre-flight — is there now a spare/secondary NVMe (not the boot drive) on any dev machine, needed for SPDK/io_uring-unbind experiments and for wear-readable native passthrough?
4. **OS priority for Profiler v1.** Linux first (Nsight Systems + io_uring is the more mature, better-documented path) or Windows first (matches the existing adapter investment, but the IoRing/DirectStorage path for tensors is unproven)?
5. **Scope of "predictive early-assign."** Is the bar a demoable improvement over no-prefetch using existing vLLM/LMCache configuration plus the profiler as evidence (achievable in the timeframe above), or a from-scratch novel prefetch algorithm (a materially larger research lift, riskier against an interview timeline)?

---

## Sources consulted (2026-06-16)

- Phison aiDAPTIV+ press materials (CES 2026, GTC 2026) and the public `aiDAPTIV-Phison/aiDAPTIV` GitHub repo's hardware/software requirements
- Mooncake paper (arXiv:2407.00079) and project changelog (kvcache-ai.github.io/Mooncake)
- LMCache architecture docs (docs.lmcache.ai) and the LMCache eval paper (arXiv:2510.09665)
- vLLM blog on the native `OffloadingConnector` and the Mooncake Store integration
- DeepSeek-V3 technical report (arXiv:2412.19437) on MLA
- SpeCache (arXiv:2503.16163), Comet (arXiv:2505.07239), async KV-prefetch paper (arXiv:2504.06319)
- NVIDIA GPUDirect Storage documentation and the GeForce/cuFile compatibility limitation
- SPDK userspace-driver documentation (spdk.io) on VFIO/UIO device unbinding
- Linux io_uring NVMe passthrough (LPC2022 slides) and Windows IoRing API documentation/comparison (windows-internals.com)
- Microsoft DeepSpeed ZeRO-Infinity / ZeRO-Inference documentation and blog posts
