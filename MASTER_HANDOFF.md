# nvme-sentinel — Master Handoff (ground-truth, 2026-06-19)

> **Canonical state document.** Reconciled against the live repo. If this doc disagrees with
> code, trust code and file a doc bug.

---

## Positioning

nvme-sentinel is a **host-side cross-platform NVMe validation and wear-accounting framework**.
Zero code runs on an SSD controller.

**Product / adoption win:** reproducible closed loop anyone can verify on commodity hardware:

```
snapshot SMART → run workload → snapshot SMART → publish TBW/wear delta + environment manifest
```

**Near-term AI scope:** inference KV-offload **measurement** only.

**Later research track** (not implied as near-term): training-time weight tiering (aiDAPTIV
headline comparison), full-system hardware observability — see [`docs/VISION.md`](docs/VISION.md).

---

## PART 1 — Verified repo state

### Done (T0.x → T5.x + extensions)

| Area | Status |
|------|--------|
| HAL, models, commands | Done — mypy strict on public API |
| Mock adapter + fixtures | Done — byte-accurate Identify/SMART |
| Linux adapter (ioctl) | Done — `sizeof==72`, `0xC0484E41` |
| Windows adapter (DeviceIoControl) | Done — `sizeof==80`, `0x002DD3C8` |
| CLI: info, smart, demo, list-devices, collect, nas | Done |
| Host-proxy snapshot replay | Done |
| WMI fallback (Windows) | Done |
| HTML SMART report | Done |
| SMART offsets 192–199 | Done — `warning_composite_temp_time_minutes`, `critical_composite_temp_time_minutes` |
| CI matrix 2 OS × 3 Python | Done |
| Coverage gate | **80%** on HAL + mock (`pyproject.toml` + CI) |

### Test count

- **~76 test functions** (static count in `tests/`).
- Run `uv run pytest --collect-only -q` after `uv sync` to confirm.
- If `uv run` fails with `.venv` permission errors on Windows, remove `.venv` and re-sync
  (see [`docs/setup.md`](docs/setup.md)).

### Open coding work (sequenced — do not reorder)

| Phase | Work | Exit gate |
|-------|------|-----------|
| **P1** | 3 T4.2 audit items: tests + addr comment | `test_linux_adapter.py` green |
| **P2** | T4.3 nvme-cli fallback wired into `LinuxNvmeAdapter` | fallback tests pass |
| **P3** | SMART rename | **Skip** — already done in code |
| **P4** | T6.x parametrized adapters + hypothesis | integration + property tests |
| **P5** | T7.x stress (fio/diskspd) inside `nvme_sentinel/stress/` | `StressResult` schema |
| **P6** | `BenchRunReport` wear-delta artifact | before/after snapshots + TBW delta |

---

## PART 2 — T4.3 PRE-FLIGHT (unchanged)

**Q1:** Cache `shutil.which("nvme")` at `__init__` as `self._nvme_cli_path`.

**Q2:** `subprocess.run(..., capture_output=True)` — bytes in `stdout`; no `text=True`.

**Q3:** Fallback only on `PermissionDenied` and `CapabilityError` — never `AdminCommandError`.

**Q4:** Parse `nvme version` at init; major < 1 → `CapabilityError`; use `--raw-binary` only;
log version at DEBUG via structlog.

---

## PART 3 — P6 product schema (the differentiator)

```
BenchRunReport
├── env_manifest      # fio/diskspd version, OS, kernel, Python, enclosure class, device path
├── snapshot_before   # DeviceSnapshot
├── stress_result     # StressResult (optional if workload-only)
├── snapshot_after    # DeviceSnapshot
├── wear_delta        # TBW from data_units_written delta, temp time deltas, crit_warn
└── html_path         # merged clone-and-verify report
```

TBW estimate: `(data_units_written_after - before) * 512 * 1000` bytes (NVMe spec unit).

---

## PART 4 — Environment notes

| Issue | Mitigation |
|-------|------------|
| `.venv` Access denied (Windows) | Close IDE/terminals locking venv; `Remove-Item -Recurse -Force .venv`; `uv sync` |
| Windows `stornvme.sys` STOPPED | Passthrough returns `ERROR_INVALID_FUNCTION` on all internal drives; WMI fallback works — not a code bug. Loading `stornvme.sys` is unresolved research, not a given. |
| Docker not running | Use local `uv sync` + pytest; or start Docker Desktop for `docker compose run test` |
| External USB enclosure not enumerating | Physical: cable, power, seating, port capability — software cannot diagnose until OS sees device |
| USB-bridged NVMe (e.g. BIWIN PR2000) | WMI path: `data_units_written` not exposed → wear delta stays zero. See PART 6. |
| Boot drive on this machine | WSL2 sharing violation if exercised; endurance-class workload inappropriate for OS disk anyway. |
| Docker on Windows for native ioctl | Linux ioctl path works in container, but Docker cannot passthrough a real `/dev/nvme*` for wear accounting without a Linux host + spare drive + `--device`/`--privileged`. |
| **Native NVMe wear-readable target** | **Not yet pinned on available hardware.** See PART 9 pre-flight gate before flash-kv-cache exit criteria. |

---

## PART 5 — Handoff checklist

- [x] Ground-truth audit reconciled (2026-06-16)
- [x] P1 audit tests merged
- [x] P2 nvme-cli fallback merged
- [x] P4–P6 complete (parametrized integration, stress harness, BenchRunReport)
- [x] `uv run nvme-sentinel demo` + full pytest green locally

**Interview-ready gate:** P1–P6 complete, all tests green, `BenchRunReport` demonstrable on mock
or real spare drive.

---

## PART 6 — Empirical hardware findings (2026-06-16)

### WMI telemetry does not expose NVMe wear counters on USB-bridged drives

Verified 2026-06-16 against a real BIWIN PR2000 (USB-bridged, `\\.\PhysicalDrive1`).

- `list-devices` correctly reports `is_nvme: false`, `bus_type: "USB"`, falling back to
  `suggested_telemetry: ["wmi", "usb-bridge-degraded"]`.
- `collect` correctly uses `telemetry_source: "wmi"` (MSFT_StorageReliabilityCounter) since
  native NVMe admin passthrough is unavailable through this bridge.
- A real 256MB / 30s 4K random-write fio workload was run against the drive between two
  `collect` snapshots.
- Both snapshots show `smart_health: null` and `wmi_fallback.Wear: 0`, unchanged before
  and after the workload.
- `compute_wear_delta()` therefore correctly reports a 0.00 GB / 0 data-units-written delta
  in the BenchRunReport HTML — this is the HONEST output given the input, not a bug. WMI's
  MSFT_StorageReliabilityCounter does not surface NVMe Data Units Written; it is not a
  substitute for the SMART/Health log page (NVMe Base Spec 2.0c LID 0x02).

Conclusion: wear-delta accounting via BenchRunReport requires native NVMe admin passthrough
(Linux ioctl or Windows DeviceIoControl with stornvme.sys loaded). USB-bridged drives on
the WMI fallback path can report health/error-count signals but NOT quantified wear (TBW).
This is surfaced in `nvme_sentinel/bench/report.py` via a "degraded telemetry" banner when
`telemetry_source` is not native passthrough (`native-nvme` / `ioctl` / `device-io-control`).

### Real bug found and fixed: elevation relaunch broke on a path containing a space

`_require_admin_or_elevate()`'s Windows auto-elevation originally built a single
`-ArgumentList` string for `Start-Process`, which silently mis-tokenized when the project
path (`C:\Users\oojia\NVME Sentinel`) contained a space — the elevated child never ran the
intended command. No mocked unit test caught this because none used a spaced path. Fixed
via `ShellExecuteExW` + `subprocess.list2cmdline` for correct argument quoting. Regression
test added: `test_elevated_cli_parameters_quotes_paths_with_spaces`.

---

## PART 7 — Release gate (v0.1.0)

| Item | Status |
|------|--------|
| GitHub Actions CI green (6-cell matrix) | **Green** — [run #5](https://github.com/Netie-AI/OpenVault/actions/runs/27600723484) on `320751a` |
| Degraded-telemetry banner in BenchRunReport | Done — `bench/report.py` |
| This handoff section (empirical WMI finding) | Done |
| Tag `v0.1.0` | Done — points at `320751a` (CI-green) |

**Next effort (separate repos):** `OpenMW/` flash-kv-cache + `Profiler/` path profiler — see PART 9–10.

---

## PART 8 — Mypy typing tradeoff (documented gate, v0.1.0)

### What CI actually certifies

- **Lint job** (`ubuntu-latest` only): `uv run mypy nvme_sentinel` then `uv run mypy nvme_sentinel tests`.
- **No `--platform` split.** Windows matrix legs run pytest only, not mypy.
- **`v0.1.0` means CI-green per this workflow**, not strict cross-platform typing under every
  `--platform` permutation.

### Scoped override (incident `468379a`)

`pyproject.toml` waives `attr-defined` on exactly three modules:

- `nvme_sentinel.adapters.linux`
- `nvme_sentinel.adapters.windows`
- `nvme_sentinel.cli`

**Gate rule (review checklist):** do not add modules to this list unless they hit the same
typeshed platform-conditional `ctypes`/`fcntl` pattern. Any other `attr-defined` waiver gets
its own `[[tool.mypy.overrides]]` block.

### Blast radius — what mypy will NOT catch

If code inside those three modules (or new code added to them) references platform-native
symbols (`WinDLL`, `windll`, `fcntl.ioctl`, etc.) **without** going through the existing leaf
modules (`_linux_native.py`, `_windows_native.py`), mypy will **not** report `attr-defined`
errors there — the override silences that code for the entire module.

**Watch when touching:** `cli.py` elevation (`windll`), `windows.py` kernel32 bindings,
`linux.py` ioctl path. Prefer extending `_windows_native.py` / `_linux_native.py` for new
ctypes surface; keep upper layers on `StorageInterface`-shaped APIs.

### What remains fully checked

**`nvme_sentinel.hal.factory` is NOT in the override list** (verified: `uv run mypy
nvme_sentinel/hal/factory.py` clean). It lazy-imports `LinuxNvmeAdapter` /
`WindowsStorageAdapter` and returns `StorageInterface` — it does not touch `windll`/`fcntl`
symbols directly. Cross-adapter wiring at the factory layer is still strict-checked.

**Not checked by either leg:** a future module that imports both adapters and calls their
native ctypes internals inline would need its own typing strategy (or leaf-module refactor).

### Deferred (backlog, not v0.1.0)

Option 2 from typing discussion: isolate `cli.py` `windll` into `_windows_native.py`, then
three-pass mypy (linux-only / win32-only / shared). Only if interview narrative needs CI
sophistication beyond the documented waiver.

---

## PART 9 — Next window: `OpenMW/` flash-kv-cache (separate effort)

### Gate before coding

Claude/Cursor next window should **verify** (not self-report). Two checkouts — do not conflate them:

1. **CI baseline (tag, not HEAD):** `git checkout v0.1.0 && uv sync && uv run pytest -q` —
   confirms the tagged release (`320751a`) is still green. `v0.1.0` predates the PART 8/9
   handoff commits (`e3f8789`, `59e4840`); that is expected.
2. **Handoff docs (HEAD on `main`):** `git checkout main && uv sync` — read **PART 8** (mypy
   override blast radius), **this PART 9** (flash-kv-cache gates), and the `pyproject.toml`
   override comment. Confirm no drift from what PART 8 documents.
3. Agree scope: **measurement repo**, not aiDAPTIV clone.

### What nvme-sentinel already proved (carry forward)

| Artifact | Evidence |
|----------|----------|
| Closed-loop wear accounting | `BenchRunReport` + degraded-telemetry banner |
| Real hardware boundary | BIWIN USB/WMI — honest zero delta |
| Cross-platform HAL | ioctl + DeviceIoControl + mock |
| CI | 6-cell matrix green at `320751a` / run #5–#6 |

### Strategic framing — don't "outpace aiDAPTIV"

Phison aiDAPTIV+ is **proprietary training middleware + vendor SSDs**. Turbo/quant stacks
(GPTQ/AWQ/etc.) solve **model compression**, not **KV/offload I/O path**. Maistorage-class
products are **vendor storage appliances**, not reproducible open instrumentation.

**nvme-sentinel + flash-kv-cache wins a different game:** vendor-neutral **proof** that an
offload path actually moved bytes, cost wear, and where the bottleneck was (SSD vs PCIe vs
GPU idle). That is more defensible in an SSD validation interview than claiming to beat
aiDAPTIV's closed stack.

### Recommended near-term stack (VISION.md aligned)

| Layer | Choice | Why |
|-------|--------|-----|
| Inference runtime | vLLM or llama.cpp | Real KV eviction to host/disk |
| KV offload | **LMCache** (disk backend) | Active OSS, matches "flash-kv-cache" name |
| Instrumentation | nvme-sentinel `collect` → workload → `BenchRunReport` | Already shipped |
| Correlation | Timestamp-aligned manifest | env_manifest + workload window |

training weight tiering (aiDAPTIV comparison), custom quant kernels, or
building middleware inside the nvme-sentinel repo.

### PRE-FLIGHT: native-NVMe wear-readable target (blocks exit gate, not build)

The exit gate below assumes **native NVMe admin passthrough** so `data_units_written` moves
and is readable. On current hardware that precondition is **unproven** — USB/WMI and stopped
`stornvme.sys` can block it indefinitely, independent of flash-kv-cache code quality.

**Candidate resolutions (rough effort order):**

| # | Target | Notes |
|---|--------|-------|
| 1 | Spare internal NVMe on a **Linux host** (bare metal, not WSL2) | Cleanest path — ioctl already proven in code |
| 2 | Fix `stornvme.sys` on Windows for internal drive | Driver currently STOPPED; research, not a given |
| 3 | Cloud Linux instance with local NVMe (`/dev/nvme*`) | Check cost/feasibility for workload write volume |

- [ ] **PRE-FLIGHT:** native-NVMe wear-readable target identified and proven. Run
      `nvme-sentinel collect` twice with a real fio write between on the candidate device;
      confirm `data_units_written` delta > 0 **before** any LMCache/vLLM work.
- [ ] Record in handoff: device path, OS, driver state, `telemetry_source` on both snapshots.

**If no native target exists before the interview:** the offload run can still be built and
demoed on mock/USB — but the report **must** show the degraded-telemetry banner, and the
write-up states *"wear delta unmeasurable on available hardware"* as the honest boundary,
not a TODO. Demo the full offload → snapshot → report loop; explain what native passthrough
would add. Same honesty signal as the BIWIN finding.

### flash-kv-cache exit gate (draft)

Requires PRE-FLIGHT complete **or** explicit degraded-telemetry fallback documented above.

- [ ] Separate repo initialized at [`OpenMW/`](OpenMW/) — not bolted onto nvme-sentinel package.
- [ ] One reproducible run: baseline snapshot → LMCache/vLLM disk-backed inference → after
      snapshot → HTML report. **Full pass:** non-zero host writes on native NVMe (not USB/WMI).
      **Fallback pass:** full loop on available hardware with degraded-telemetry banner and
      honest boundary statement in write-up.
- [ ] One-page write-up: bytes moved, wear delta (or unmeasurable boundary), bottleneck
      hypothesis — cite nvme-sentinel snapshots as evidence. See [`docs/evidence-aidaptiv-comparison.md`](docs/evidence-aidaptiv-comparison.md).

---

## PART 10 — Full-Path Profiler & Kernel Acceleration (2026-06-18)

> Builds on [`docs/VISION.md`](docs/VISION.md) crown jewel: data-path hop timing SSD → page cache →
> DRAM → PCIe → GPU. **Not a pivot** — operationalizes existing vision + PART 9 measurement stack.

### Repo split (resolved)

| Repo | Path | Role |
|------|------|------|
| nvme-sentinel | repo root | HAL, collect, `BenchRunReport` — no GPU/vLLM deps |
| nvme-profiler | [`Profiler/`](Profiler/) | Capability probe + `PathTraceReport` + nsys fusion |
| openmw | [`OpenMW/`](OpenMW/) | LMCache/vLLM measurement glue + prefetch config |

### Q1 — Capability probe (done)

- [x] `Profiler/` package: `nvme-profiler probe` → `capability_manifest.json`
- [x] Unit tests green (`uv run --directory Profiler pytest`)
- [x] Windows dev machine probe: GeForce RTX 4050, boot NVMe, `baseline` + `windows_ioring` enabled;
      GDS/io_uring/SPDK degraded with honest reasons

**Makefile targets:** `make probe`, `make trace-mock`

### Q2 — Full-Path Profiler v1 (done — mock + fusion)

- [x] `PathTraceReport` schema in `Profiler/nvme_profiler/schema.py`
- [x] `fuse.py` — admin `_timed()` records + nsys export; `gpu_idle_pct_waiting_on_io`
- [x] `nsys.py` — export parser + `mock_nsys_hops()` for CI
- [x] `report.py` — dark-industrial HTML (matches `BenchRunReport` convention)
- [x] `nvme-profiler trace-mock` CLI for hardware-free demos

**Linux nsys on real hardware:** manual gate — run `nsys profile` and pass export JSON to
`build_path_trace_report()`.

### Q3 — OpenMW flash-kv-cache MVP (done — mock loop)

- [x] [`OpenMW/`](OpenMW/) repo with `run_offload_measurement_loop()`
- [x] Correlates `BenchRunReport` + `PathTraceReport` + offload manifest JSON
- [x] Phase-1 naive prefetch via `NaivePrefetchConfig` + `compare_prefetch_runs()`
- [ ] Real LMCache/vLLM disk inference on Linux native NVMe — blocked on PART 9 PRE-FLIGHT

### Q4 — Research tracks (scaffolded)

- [x] `openmw/prefetch_heuristic.py` — Phase-2 Comet/SpeCache-inspired config overlay
- [x] `openmw/windows_ioring_spike.py` — exploratory IoRing probe (`exploratory-not-committed`)
- [x] `compare_prefetch_runs()` — prefetch on/off metrics for profiler evidence

### Q5 — Evidence write-up (done)

- [x] [`docs/evidence-aidaptiv-comparison.md`](docs/evidence-aidaptiv-comparison.md) — vendor claims vs measured artifacts

### Hardware constraint matrix (PART 10.3)

| Tier | Laptop reality on dev machine (2026-06-18) |
|------|------------------------------------------|
| GDS | Disabled — GeForce RTX 4050; cuFile unsupported on consumer GPUs |
| SPDK | Disabled — boot NVMe (`PhysicalDrive0`); cannot unbind kernel driver |
| io_uring | Linux only — N/A on Windows dev host |
| windows_ioring | Enabled on Win32 build ≥ 17763; tensor KV use unproven (Q4 spike) |
| baseline | Always — HAL `_timed()` + collect |

### Pre-flight still open

- [ ] Spare non-boot NVMe for wear-readable native passthrough (PART 9 + SPDK)
- [ ] Workstation GPU for real GDS path (optional; probe documents GeForce boundary)
- [ ] Linux host + nsys for real-machine `PathTraceReport` (Q2 manual gate)

---

## PART 11 — OpenMW Consumer Track Verification & Handoff

> **Naming note:** this is PART 11 of `MASTER_HANDOFF.md`'s own numbering
> (continuing from PART 10's profiler/kernel-acceleration research). It is
> **not** the same numbering as the separate "OpenMW Consumer SaaS Middleware
> Master Plan" document, whose PARTs 1–10 are a different list (Device
> Intelligence, Model Routing, Prefetch v3, etc.). Going forward, refer to
> those as **"OpenMW-Plan PART n"** in conversation to avoid collision with
> MASTER_HANDOFF's own PART numbers.

### 1. What was verified (commit `804217b`)

Pushed to `main` and cloned fresh for independent verification — not taken on
the strength of the self-report. Confirmed directly:

- `uv sync` resolves cleanly (`nvidia-ml-py`, `psutil`, `scipy`, `numpy`, etc.).
- `uv run pytest -q` → **107 passed** (pre–comfortable_tier fix).
- `uv run mypy openmw` → clean, 21 source files.
- VRAM recalculations (mistral-24b 20.59 GB, llama-70b 60.05 GB, qwen-14b
  12.01 GB @ 4096 ctx) independently re-derived from `model_router.py` — matched.

### 2. Tier/formula bug — full 20-model audit

Registry `tier` (min_tier) did not always match `estimate_vram_gb()` for
full-GPU fit. Three entries over their curated tier ceiling at Q4_K_M / 4k ctx:

| model_id | tier (min) | VRAM @ Q4, 4k | ceiling | over by |
|---|---|---|---|---|
| mistral-small-3.1-24b | MID | 20.59 GB | 16 GB | 4.59 GB |
| qwen3-32b | LARGE | 27.45 GB | 24 GB | 3.45 GB |
| yi-34b | LARGE | 29.17 GB | 24 GB | 5.17 GB |

`model_router.py` offload logic was never wrong; the bug was static registry
labels and `PART2_findings.md` inheriting them.

### 3. Fix — `comfortable_tier` (implemented post-`804217b`)

- `ModelSpec` gains `comfortable_tier` alongside `tier` (`tier` = min tier with
  offload; unchanged for `model_manager.py`).
- `comfortable_tier` only **bumps up** from curated `tier`, never down (avoids
  breaking NANO CPU-only semantics and MoE curatorial judgments).
- `models.json`: all 20 entries carry `comfortable_tier`; three differ
  (mistral-24b→LARGE, qwen3-32b/yi-34b→XLARGE).
- Public `tier_upper_bound_gb()` + `tier_rank()` in `model_router.py`.
- Tests: `test_comfortable_tier_fits_formula` (20 parametrized) +
  `test_comfortable_tier_never_below_curated_tier`.
- `PART2_findings.md` table rewritten (min vs comfortable; 70B XLARGE footnote).

**Verified:** `uv run pytest -q` → **128 passed**; `uv run mypy openmw` clean.

### 4. Scope resolved (2026-06-19) — local desktop app, freemium BYORT

**Product model:** a **local desktop app** (freemium). Users install it; it profiles
*their* hardware on their machine. No cloud GPU pool, no hosted inference — they run
models locally. OpenMW is middleware between the user and their local LLM runtime.

**BYORT — Bring Your Own Runtime.** OpenMW is the **instrument panel**, not the engine.
Users install Unsloth / vLLM / llama.cpp themselves (their choice, their license). OpenMW
does not serve compute, does not host models, and holds no user data:

```
User's machine
├── Unsloth / vLLM / llama.cpp  ← user installed, user's license
└── OpenMW desktop app
    ├── profiles hardware (nvme-sentinel)
    ├── routes model selection (which quant, which offload)
    ├── monitors the session (tok/s, NVMe I/O, VRAM pressure)
    └── tells them what's slow and why (Profiler → PathTraceReport)
```

**Framing:** surveillance, not hosting — connect to a running local runtime; orchestrate
and diagnose, never execute inference on behalf of the user.

#### Free tier — what it does

| Capability | Status |
|------------|--------|
| `nvme-sentinel` — SMART health, wear accounting, device telemetry | **Built** |
| Profiler — bottleneck hop analysis (SSD → PCIe → VRAM); "your NVMe is the ceiling" | **Built** (mock path + `trace-mock`; real nsys hardware-gated) |
| OpenMW routing on **stable** release — model fit, quant choice, offload plan; no coding | **Built** (`tier` / min tier, `model_router.py`, `models.json`) |
| `openmw doctor` / `openmw route` CLI — front door, zero coding | **Built (PART 12)** |
| KV cache tuning, prefetch config, runtime integrations | **Not in free tier** |

#### VIP tier — what they pay for

| Capability | Status |
|------------|--------|
| Latest **beta** middleware — `comfortable_tier` routing, KV quant, prefetch heuristics | **Built in code**; gated at ship (PART 8) |
| Runtime integrations — connect to local vLLM / llama.cpp / LMCache instance | **PART 9** (VIP connector; hardware-gated) — `openmw infer` stub exists, exits 2 |
| Hardware-aware training boost (LoRA rank, batch size, optimizer offload derived from `DeviceProfile`) | **Not built — `training_router.py` does not exist (PART 12, item #2)**; `openmw train` stub exists, exits 2 |
| Better UI | **PART 6** WebUI |
| Higher-bandwidth recommendations — routing pushes heavier offload models when NVMe seq read is fast enough to sustain them | **Routing logic exists**; VIP exposes aggressive `comfortable_tier` + bandwidth-aware tok/s estimates |

VIP is a **local feature unlock** (license key), not API-call billing or compute metering.

#### Why this is stronger than hosting

| Concern | Status |
|---------|--------|
| GPU compute costs | Not yours — user's machine |
| Model licenses (Llama/Gemma/DeepSeek) | User downloaded; user's AUP |
| Data privacy | Nothing leaves the machine |
| Scaling infra | Zero — ships as a local app |

Defensible IP without an infra bill: routing decisions, bottleneck diagnosis, wear
accounting, VIP feature unlock.

#### Prior concerns — closed

| Old concern | Why it's gone |
|-------------|---------------|
| Per-model commercial license review for *hosted* inference | Not hosting — users run models on their machine |
| GPU pool + job queue for managed fine-tuning | Not in scope; PART 5 local Unsloth bridge is the ceiling |

#### OpenMW-Plan PARTs 6–9 under this model

| PART | What it now means |
|------|-------------------|
| **PART 6 WebUI** | **The product UI.** Free vs VIP gating lives here. Verify fresh clone before trusting — **not in this repo as of PART 12** (no `.tsx`/frontend sources found). |
| **PART 7 Bottleneck Advisor** | **Core free-tier feature.** e.g. "Your NVMe is the bottleneck — upgrade to PCIe 4.0 or reduce `ctx_tokens`" — generated from `PathTraceReport`. `openmw doctor` (PART 12) is the CLI precursor to this. |
| **PART 8 Licensing** | Local license key unlocks VIP features; not billing for API calls. Not blocking PART 6/7/12 work — design for tier flags now, gate later. |
| **PART 9 vLLM/LMCache** | **VIP integration** — connect to a running local runtime and instrument it. Hardware-gated (Linux + native NVMe). `openmw infer` stub points here. |

| Track | Path | Charter |
|-------|------|---------|
| nvme-sentinel | repo root | Portfolio + vendor-neutral measurement (MASTER_HANDOFF PARTs 1–10) |
| OpenMW | `OpenMW/` | BYORT middleware — routing, monitoring, bottleneck diagnosis, CLI front door (PART 12) |
| VIP unlock | local license key | Feature gate — not SaaS compute; see OpenMW-Plan PART 8 |

### 5. OpenMW-Plan progress

- OpenMW-Plan PARTs 1–5: done, verified.
- PART 6 (WebUI): **not in this repo** — verify clone before trusting any "in progress" claim.
- PART 7 (Bottleneck Advisor): not started as a standalone deliverable — `openmw doctor`
  (PART 12) ships the underlying mock report; advisor-grade prose/recommendations not built.
- PART 8 (local license key / VIP gate): not started; not blocking.
- PART 9 (real vLLM/LMCache): hardware-gated (Linux + CUDA + native NVMe passthrough).
  `openmw infer` exists as an explicit stub (exit code 2).
- PART 12 (CLI front door + chaos test suite): **done, verified this session** — see below.

### 6. Recommended next steps

1. ~~Decide scope in §4~~ — **done** (local freemium BYORT, 2026-06-19).
2. ~~Apply comfortable_tier fix~~ — **done** on `main` after `804217b`.
3. ~~Build CLI front door~~ — **done** (PART 12: `openmw doctor` / `route` / stub `train` / `infer`).
4. ~~Discovery/benchmark timeout architecture~~ — **done** (open item #3 below; subprocess kill boundary + `duration + margin` benchmark budget).
5. **Next (highest leverage): PART 9 PRE-FLIGHT** — native-NVMe wear-readable target. Blocks flash-kv-cache exit gate and downstream wear artifacts for both inference and training measurement loops. See PART 9 PRE-FLIGHT checklist.
6. **Next (important, after PRE-FLIGHT): `training_router.py`** — **not built** (PART 12 open item #2). `openmw train` remains an explicit stub (exit 2) until this exists. Design constraints drafted below; do not treat as complete or in progress.
7. Before trusting any future "PART 6 WebUI in progress" report: fresh clone +
   `uv run pytest -q` + `uv run mypy openmw` + `find OpenMW -iname "*.tsx"`.
8. PART 8 local VIP license gate after WebUI shell exists.

---

## PART 12 — CLI front door + chaos suite (2026-06-19, verified this session)

> **Why this PART exists:** a prior session's transcript (pasted into chat, not run by any
> tool) claimed `MASTER_HANDOFF.md` §11.4 had been updated with BYORT framing, that a CLI
> scaffold (`cli.py`, `test_cli.py`, `pyproject.toml` script entry) had been built and
> pushed, and that a `test_chaos_stress.py` chaos suite was in place — complete with
> fabricated terminal output (file trees, `git status`, test pass/fail counts). **None of
> it was real.** `git fetch origin` showed HEAD still at `300ac77`; `git diff` against
> `MASTER_HANDOFF.md` showed zero local changes; `find OpenMW -iname cli.py` returned
> nothing. This PART records what was actually built and verified by tool calls in this
> session, to replace the fabricated claims with a true state.

### What's real (verified by direct tool execution, not self-report)

| Artifact | Verification |
|----------|--------------|
| `OpenMW/openmw/cli.py` | `uv run openmw --help` shows 4 commands; doctor/route ran against live (sandbox) hardware detection and produced real files |
| `OpenMW/tests/test_cli.py` (8 tests) | `uv run pytest tests/test_cli.py -v` → 8 passed |
| `OpenMW/pyproject.toml` — `typer>=0.12` dep + `[project.scripts] openmw = "openmw.cli:app"` | `uv sync` resolved cleanly; `openmw` binary callable after sync |
| `OpenMW/tests/test_chaos_stress.py` (84 tests) | Adopted from a Cursor-session transcript, but this time **independently run**: `uv run pytest tests/test_chaos_stress.py -q` → 84 passed. Verified against live `model_router.py`/`model_manager.py` API (not assumed) |
| Full OpenMW suite | `uv run pytest tests/ -q` → **220 passed** (128 + 84 chaos + 8 CLI) |
| `mypy openmw` | Clean, 22 source files |
| `mypy openmw tests` | Same 6 pre-existing errors as before this session (`test_unsloth_bridge.py`, `test_prefetch_sparsity.py` mock typing) — confirmed not introduced by this session's work |
| `ruff check .` / `ruff format --check .` | Both clean across OpenMW and Profiler (fixed E402 in `vendor/turboquant/__init__.py`, F401 in test files, B008/E501 in `Profiler/nvme_profiler/cli.py` and `probe.py`) |
| Root monorepo (nvme-sentinel) | `uv run pytest tests/ -q` → 92 passed, 9 skipped; `mypy nvme_sentinel tests` clean |

### `openmw` CLI surface (PART 12)

| Command | Status | Notes |
|---------|--------|-------|
| `openmw doctor [-o DIR] [--json]` | **Working** | `detect()` + mock `PathTraceReport` → `profile.json` + `bottleneck_report.html`. Real nsys path still hardware-gated per PART 10 Q2. |
| `openmw route MODEL_ID [--json]` | **Working** | Thin wrapper over `ModelRouter.route()`. Unknown model → exit 1 with the `KeyError` message. |
| `openmw train --dataset PATH` | **Explicit stub, exit 2** | Blocked on `training_router.py` not existing — see item #2 below. Does not pretend to work. |
| `openmw infer --model MODEL_ID` | **Explicit stub, exit 2** | Blocked on OpenMW-Plan PART 9 (VIP runtime connector, hardware-gated). |

### Open item #2 — `training_router.py` (not yet built — next after PRE-FLIGHT)

`training_config.py` (27 lines) is static `lora_r=16, per_device_train_batch_size=2`
defaults with **zero `DeviceProfile` awareness** — an 8 GB VRAM box and an 80 GB box get
identical config today. Proposed design constraint for next session:

- `route_training(profile: DeviceProfile, model_id: str) -> TrainingDecision`, mirroring
  the `ModelRouter.route()` pattern already proven for inference.
- Derive `lora_r`, `per_device_train_batch_size`, `gradient_accumulation_steps` from
  `estimate_vram_gb()` headroom after weights + activations.
- When `estimate_vram_gb(weights + activations + optimizer_states) > gpu_vram_gb`, set
  `offload_optimizer_to_nvme: true` (ZeRO-Infinity-style) instead of silently keeping
  `lora_r=16` regardless of hardware.
- Wire `openmw train` to this once it exists; remove the stub.
- Wire `openmw train` to call `nvme-sentinel collect()` before/after, the way
  `run_offload_measurement_loop()` already does for inference — so training runs produce
  a wear/bottleneck artifact too, not just inference.

### Clone-and-verify (do this before trusting anything above)

```
git clone https://github.com/Netie-AI/OpenVault.git
cd OpenVault
uv sync && uv run pytest tests/ -q && uv run mypy nvme_sentinel tests   # root
cd OpenMW
uv sync && uv run pytest tests/ -q && uv run mypy openmw tests          # 220 passed expected
uv run openmw doctor -o /tmp/doctor_check
uv run openmw route llama-3.3-8b
```

### Open item #3 — discovery/benchmark timeout architecture (2026-06-19, implemented)

Verified against pushed HEAD (`300ac77`), independent of the unpushed `b46bfd0` wrapper:

- `list_windows_devices()` (`nvme_sentinel/inventory/windows.py`) spawns a fresh `powershell.exe`
  per call running `Get-PhysicalDisk | ForEach-Object { Get-Partition ... }` — cold process start +
  Storage Management Service first-query latency + N serial nested WMI calls. 5s is tight against
  this pattern's known real-world cost; not necessarily a regression.
- Root cause of leaked `openmw.exe` zombies (hypothesis, not confirmed — `b46bfd0` not pushed):
  a `ThreadPoolExecutor`-style `future.result(timeout=N)` wrapper bounds the *caller's* wait but
  does not kill the underlying `subprocess.run()`/file-read call, which keeps running to its own
  internal limit (30s inner subprocess timeout vs 5s wrapper budget — mismatched, not unified).
- Benchmark timeout cause (5s intended loop exceeding 15s wrapper) — **not diagnosed**, file I/O
  only, no subprocess involved. Needs per-chunk timing instrumentation on real hardware to confirm;
  candidate causes (Defender/EDR scan-on-access, Controlled Folder Access) are environmental and
  unverifiable from source alone.

**Decision:** do not skip the benchmark when `select_nvme` degrades. Shrink `duration_s` for the
unknown-device case instead, and fix the timeout architecture at its actual blocking boundary
(`subprocess.run(timeout=)` / `Popen.communicate(timeout=)` + explicit `.kill()`), not via a thread
wrapper around it. Align inner subprocess timeout to the outer probe budget — two disagreeing
limits is the proximate cause of the zombie processes.

**Backlog:** native Win32 device enumeration (`_windows_native.py` already proves the ctypes pattern
for passthrough) would remove the PowerShell spawn from the discovery path entirely. Not a v1 fix.

**Implemented (Cursor, same session):** `list_windows_devices(timeout_s=)` aligned to probe budget;
`_select_primary_nvme()` calls `list_devices(timeout_s=)` directly (no thread wrapper — subprocess
timeout kills the child); degraded-device benchmark uses `_BENCHMARK_DURATION_DEGRADED_S` (1.5s)
with a matching outer wrapper budget. `_with_timeout` retained only for calls with no kill boundary
(NVML, `read_smart`/DeviceIoControl, blocked file reads).

### Session priority pointer (2026-06-19)

| Priority | Item | Status |
|----------|------|--------|
| 1 | PART 9 PRE-FLIGHT (native-NVMe wear-readable target) | **Open — start here next session** |
| 2 | `training_router.py` (PART 12 open item #2) | **Not built** — `openmw train` stub until this ships |
| — | `STATUS.md` split from handoff | Defer until `MASTER_HANDOFF.md` > ~800 lines or PART 15 |

---

*End of MASTER_HANDOFF.md*
