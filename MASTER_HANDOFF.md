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

*End of MASTER_HANDOFF.md*
