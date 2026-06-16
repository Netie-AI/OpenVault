# nvme-sentinel — Master Handoff (ground-truth, 2026-06-16)

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
| Windows `stornvme.sys` STOPPED | Passthrough returns `ERROR_INVALID_FUNCTION`; WMI fallback works — not a code bug |
| Docker not running | Use local `uv sync` + pytest; or start Docker Desktop for `docker compose run test` |
| External USB enclosure not enumerating | Physical: cable, power, seating, port capability — software cannot diagnose until OS sees device |

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

**Next effort (separate repo/track):** `flash-kv-cache` — see PART 8.

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

## PART 9 — Next window: `flash-kv-cache` (separate effort)

### Gate before coding

Claude/Cursor next window should **verify** (not self-report):

1. `git checkout v0.1.0 && uv sync && uv run pytest -q` — baseline still green.
2. Read this PART 8 + `pyproject.toml` override comment — confirm no drift.
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

**Not first:** training weight tiering (aiDAPTIV comparison), custom quant kernels, or
building middleware inside the nvme-sentinel repo.

### flash-kv-cache exit gate (draft)

- [ ] Separate repo initialized from handoff, not bolted onto nvme-sentinel package.
- [ ] One reproducible run: baseline snapshot → LMCache/vLLM disk-backed inference → after
      snapshot → HTML report with non-zero host writes (on **native NVMe**, not USB/WMI).
- [ ] One-page write-up: bytes moved, wear delta, bottleneck hypothesis — cite nvme-sentinel
      snapshots as evidence.

---

*End of MASTER_HANDOFF.md*
