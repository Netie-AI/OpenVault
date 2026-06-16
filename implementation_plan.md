# nvme-sentinel — Implementation Plan

> Cross-platform Python framework for NVMe SSD validation, characterization, and health monitoring. Built to demonstrate enterprise-grade storage engineering for an SSD validation interview.

---

## 0. Win conditions (why this project exists)

This project targets an SSD validation engineering role whose JD emphasises:
- Cross-platform (Linux + Windows) APIs for SSD validation
- Test automation framework design
- Protocol/driver/OS stack knowledge (PCIe, NVMe, storage stack)
- UML design communication
- Shift-left test strategy

Every architectural decision in this document is optimised to produce **five interview signals**:

| # | Signal | Where it shows up |
|---|--------|-------------------|
| 1 | Cross-platform API discipline | HAL ABC + two concrete adapters sharing one command layer |
| 2 | NVMe protocol depth | Raw `ioctl(NVME_IOCTL_ADMIN_CMD)` + byte-accurate SMART/Identify parsers |
| 3 | Test automation maturity | Mock-first TDD, pytest parametrization matrix, ≥80% HAL coverage, GH Actions on 2 OS × 3 Py |
| 4 | UML / documentation fluency | PlantUML architecture diagram + 3-paragraph Design Decisions doc |
| 5 | Shift-left instinct | Mock adapter is product-grade, not test-only; CI catches regressions before hardware |

## 1. Non-negotiable constraints

- **Language**: Python 3.10 / 3.11 / 3.12 (matrix-tested)
- **Package management**: `uv` + `pyproject.toml` (PEP 621). No pip/poetry/conda.
- **Type safety**: `mypy --strict` on `nvme_sentinel/hal/`, `models/`, `commands/`. No `Any` in public signatures.
- **Coverage**: `pytest-cov ≥ 80%` gate on HAL + mock adapter.
- **Linting**: `ruff` (E, F, I, N, UP, B, SIM, TID, RUF) + `ruff format`.
- **CI**: GH Actions matrix `{ubuntu-latest, windows-latest} × {3.10, 3.11, 3.12}` — 6 cells, all green, no hardware dependency.
- **Docs**: PlantUML architecture diagram committed as `.puml` + rendered `.svg`; Design Decisions doc; setup guide.

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Presentation          CLI (Typer)   |   HTML Report         │
├─────────────────────────────────────────────────────────────┤
│  Application           Stress Orchestrator │ Regression Eng. │
│                        (fio / diskspd)     │ (SMART trends)  │
├─────────────────────────────────────────────────────────────┤
│  Domain                NVMe Command Layer                    │
│                        Identify Ctrl/NS · Get Log Page       │
│                        SMART · Error Info · FW Slot · PEL    │
├─────────────────────────────────────────────────────────────┤
│  HAL                   StorageInterface (abc.ABC)            │
│                        ├── LinuxNvmeAdapter   (ioctl → CLI)  │
│                        ├── WindowsStorageAdapter (DeviceIo)  │
│                        └── MockNvmeAdapter    (simulator)    │
├─────────────────────────────────────────────────────────────┤
│  OS Kernel             NVMe class driver (/dev/nvmeX \\.\SCSI) │
└─────────────────────────────────────────────────────────────┘
```

### Why a thin HAL, not a "storage god-object"

The HAL surface is **~8 methods**: `open`, `close`, `admin_passthru`, `nvm_passthru`, `get_device_info`, `list_namespaces`, `is_nvme`, `capabilities`. Everything else — SMART parsing, Identify decoding, stress job orchestration, reporting — composes above the HAL without knowing about OS specifics. This is a direct response to the JD phrase *"robust and concise test designs that can run across a multitude of test platforms"*: concise at the boundary, rich above it.

## 3. Module layout

```
nvme-sentinel/
├── pyproject.toml
├── .python-version              # pinned 3.12 for local dev
├── .cursorrules                 # agent guardrails (see task.md)
├── .pre-commit-config.yaml
├── .github/
│   └── workflows/ci.yml
├── nvme_sentinel/
│   ├── __init__.py              # __version__
│   ├── hal/
│   │   ├── interface.py         # StorageInterface ABC
│   │   ├── base.py              # BaseAdapter (retry/timing/telemetry)
│   │   ├── exceptions.py        # DeviceError, AdminCommandError, …
│   │   ├── enums.py             # OpCode, LogPageID, CNSValue, CritWarn
│   │   └── factory.py           # get_adapter() — OS autodetect
│   ├── adapters/
│   │   ├── linux.py             # LinuxNvmeAdapter (ctypes + fcntl.ioctl)
│   │   ├── windows.py           # WindowsStorageAdapter (DeviceIoControl)
│   │   └── mock.py              # MockNvmeAdapter (product-grade simulator)
│   ├── commands/
│   │   ├── admin.py             # AdminCommand builder + dispatcher
│   │   ├── identify.py          # Identify Controller / Namespace / NS list
│   │   ├── log_pages.py         # SMART / Error / FW Slot / PEL
│   │   └── features.py          # Get/Set Features (power state, LBA fmt)
│   ├── models/
│   │   ├── identify.py          # ControllerIdentify, NamespaceIdentify (pydantic)
│   │   ├── smart.py             # SmartHealthLog — 128-bit counters
│   │   ├── error_log.py         # ErrorLogEntry
│   │   └── firmware.py          # FirmwareSlotInfo
│   ├── stress/
│   │   ├── profiles.py          # JobProfile: seq_read, rand_rw_70_30, mixed
│   │   ├── fio.py               # FioRunner (JSON output parser)
│   │   ├── diskspd.py           # DiskspdRunner (XML output parser)
│   │   └── parser.py            # shared result schema
│   ├── reporting/
│   │   ├── html.py              # Jinja2 renderer
│   │   ├── charts.py            # matplotlib SMART trend charts
│   │   └── trends.py            # regression vs baseline
│   └── cli.py                   # Typer app
├── tests/
│   ├── conftest.py              # (adapter, device_path) fixtures
│   ├── unit/
│   │   ├── test_hal_interface.py
│   │   ├── test_mock_adapter.py
│   │   ├── test_identify_parser.py
│   │   ├── test_smart_parser.py
│   │   ├── test_fio_parser.py
│   │   └── test_diskspd_parser.py
│   ├── integration/
│   │   ├── test_adapter_roundtrip.py     # parametrized: mock, linux, windows
│   │   └── test_cli_smoke.py
│   └── fixtures/
│       ├── identify_ctrl_pm9a3.bin       # 4096 bytes, real capture
│       ├── identify_ns.bin               # 4096 bytes
│       ├── smart_healthy.bin             # 512 bytes
│       ├── smart_degraded.bin            # 512 bytes, non-zero crit_warn
│       ├── fio_output_sample.json
│       └── diskspd_output_sample.xml
└── docs/
    ├── README.md
    ├── architecture.puml
    ├── architecture.svg         # rendered, committed
    ├── design-decisions.md      # the 3-paragraph deliverable
    └── setup.md                 # Linux + Windows + WSL
```

## 4. NVMe domain reference (what the command layer must know)

This section exists so the coding agent does not hallucinate field offsets. Cite these values in code comments.

### 4.1 Admin opcodes (subset — what we implement)

| Opcode | Name | Why we need it |
|--------|------|----------------|
| `0x02` | Get Log Page | SMART / Error Info / FW Slot / PEL |
| `0x06` | Identify | Controller, Namespace, NS list |
| `0x09` | Set Features | Power state, APST, temp threshold |
| `0x0A` | Get Features | Read back current feature values |
| `0x10` | Firmware Commit | (optional, keep behind flag) |
| `0x11` | Firmware Download | (optional, keep behind flag) |

### 4.2 CNS values for Identify (opcode 0x06)

| CNS | Returns | Size |
|-----|---------|------|
| `0x00` | Identify Namespace (active NSID) | 4096 B |
| `0x01` | Identify Controller | 4096 B |
| `0x02` | Active Namespace ID list | 4096 B |
| `0x03` | Namespace Identification Descriptors | 4096 B |

### 4.3 Log Page IDs (opcode 0x02)

| LID | Name | Size | Notes |
|-----|------|------|-------|
| `0x01` | Error Information | n × 64 B | `elpe+1` entries in Identify Ctrl |
| `0x02` | **SMART / Health** | 512 B | **Primary target** |
| `0x03` | Firmware Slot Information | 512 B | |
| `0x04` | Changed Namespace List | 4096 B | |
| `0x05` | Commands Supported and Effects | 4096 B | |
| `0x0D` | Persistent Event Log | variable | header + events |

### 4.4 SMART Health Log (LID 0x02) — byte layout

The SMART log is **512 bytes**. Fields the framework must expose (byte offsets are inclusive):

| Offset | Bytes | Field | Type |
|--------|-------|-------|------|
| 0 | 1 | Critical Warning (bitfield) | u8 |
| 1–2 | 2 | Composite Temperature (Kelvin) | u16 LE |
| 3 | 1 | Available Spare (%) | u8 |
| 4 | 1 | Available Spare Threshold (%) | u8 |
| 5 | 1 | Percentage Used (%) | u8 |
| 6 | 1 | Endurance Group Critical Warning Summary | u8 |
| 32–47 | 16 | Data Units Read (1000 × 512 B) | **u128 LE** |
| 48–63 | 16 | Data Units Written | **u128 LE** |
| 64–79 | 16 | Host Read Commands | u128 LE |
| 80–95 | 16 | Host Write Commands | u128 LE |
| 96–111 | 16 | Controller Busy Time (minutes) | u128 LE |
| 112–127 | 16 | Power Cycles | u128 LE |
| 128–143 | 16 | Power On Hours | u128 LE |
| 144–159 | 16 | Unsafe Shutdowns | u128 LE |
| 160–175 | 16 | Media and Data Integrity Errors | u128 LE |
| 176–191 | 16 | Number of Error Information Log Entries | u128 LE |
| 192–195 | 4 | Warning Composite Temp Time (min) | u32 LE |
| 196–199 | 4 | Critical Composite Temp Time (min) | u32 LE |

**Python gotcha**: the 128-bit fields. Use `int.from_bytes(buf[32:48], 'little')` — Python ints are arbitrary precision, so no overflow. Call this out in a comment; a reviewer will notice.

**Critical Warning bitfield** (offset 0):
```
bit 0: available spare below threshold
bit 1: temperature threshold exceeded
bit 2: NVM subsystem reliability degraded
bit 3: media placed in read-only mode
bit 4: volatile memory backup failed
bit 5: persistent memory region read-only/unreliable
```

### 4.5 Linux ioctl path — the exact struct

From `<linux/nvme_ioctl.h>` (kernel uAPI, stable for 5.x+):

```c
struct nvme_passthru_cmd {
    __u8    opcode;
    __u8    flags;
    __u16   rsvd1;
    __u32   nsid;
    __u32   cdw2;
    __u32   cdw3;
    __u64   metadata;
    __u64   addr;
    __u32   metadata_len;
    __u32   data_len;
    __u32   cdw10;
    __u32   cdw11;
    __u32   cdw12;
    __u32   cdw13;
    __u32   cdw14;
    __u32   cdw15;
    __u32   timeout_ms;
    __u32   result;
};
/* sizeof == 72 bytes, no padding needed with __attribute__((packed)) in C;
   in Python ctypes use _fields_ with explicit types and _pack_ = 1.       */

#define NVME_IOCTL_ADMIN_CMD _IOWR('N', 0x41, struct nvme_passthru_cmd)
/* Numeric value on x86_64: 0xC0484E41                                     */
```

Python `ctypes.Structure` equivalent must set `_pack_ = 1` and assert `ctypes.sizeof(NvmePassthruCmd) == 72` at module load.

### 4.6 Windows path — STORAGE_PROTOCOL_COMMAND

From `ntddstor.h`:

```c
typedef struct _STORAGE_PROTOCOL_COMMAND {
    ULONG   Version;
    ULONG   Length;
    STORAGE_PROTOCOL_TYPE ProtocolType;  // ProtocolTypeNvme = 3
    ULONG   Flags;
    ULONG   ReturnStatus;
    ULONG   ErrorCode;
    ULONG   CommandLength;
    ULONG   ErrorInfoLength;
    ULONG   DataToDeviceTransferLength;
    ULONG   DataFromDeviceTransferLength;
    ULONG   TimeOutValue;
    ULONG   ErrorInfoOffset;
    ULONG   DataToDeviceBufferOffset;
    ULONG   DataFromDeviceBufferOffset;
    ULONG   CommandSpecific;
    ULONG   Reserved0;
    ULONG   FixedProtocolReturnData;
    ULONG   Reserved1[3];
    UCHAR   Command[ANYSIZE_ARRAY];       // 64 bytes for NVMe
} STORAGE_PROTOCOL_COMMAND;

#define IOCTL_STORAGE_PROTOCOL_COMMAND   0x2DD480
```

Assert `sizeof(STORAGE_PROTOCOL_COMMAND_HEADER) == 80` at module load. The 64-byte Command buffer trails the header — allocate a `ctypes.c_ubyte * (80 + 64 + data_len)` blob and compute offsets.

## 5. Technology stack

| Concern | Choice | Justification |
|---------|--------|---------------|
| Package mgmt | `uv` | 10–100× faster than pip/poetry; PEP 621 native; reproducible lockfile |
| Build backend | `hatchling` | PEP 517, lean, no setup.py |
| Validation | Pydantic v2 | Fastest parser; clean JSON I/O for reports; good for typed NVMe models |
| CLI | Typer | Type-annotated, Click under the hood, auto `--help` |
| Logging | structlog | Dict-based records; CI grep-friendly; no printf-debugging |
| Tests | pytest + pytest-cov + pytest-xdist + hypothesis | Parametrization, coverage, parallel, property tests on parsers |
| Lint/format | ruff | Single tool replaces flake8+isort+pydocstyle+black |
| Types | mypy (strict on public API) | Catches adapter contract drift |
| Hooks | pre-commit | Enforces before push |
| Docs / charts | Jinja2 + matplotlib | CI-friendly (no browser needed for matplotlib) |
| Stress | fio (Linux), diskspd (Windows) | Industry standard; both emit structured output |

**Rejected alternatives**:
- *Poetry* — slower, heavier, `uv` dominates in 2026
- *unittest* — pytest is strictly better for parametrization
- *plotly* as default — JS render needed; we offer it as an opt-in
- *paramiko / fabric* — no remote SSH in scope
- *click directly* — Typer wraps it with types; cleaner

## 6. Phased delivery plan

Each phase is atomic, verifiable, and composable. Phases 1→2 are swapped relative to a "write the real thing first" instinct: we build the **mock adapter before the real one** so every downstream layer is TDD-driven.

| Phase | Deliverable | Est. agent-hours | Gate |
|-------|-------------|------------------|------|
| 0 | Repo bootstrap, uv, pyproject, CI skeleton, pre-commit | 0.5 | `uv sync && uv run pytest --collect-only` clean |
| 1 | HAL contracts: ABC, exceptions, enums, Pydantic models | 1.0 | `mypy --strict` passes; models round-trip bytes |
| 2 | MockNvmeAdapter + captured binary fixtures | 1.5 | Mock returns byte-accurate Identify/SMART |
| 3 | NVMe command layer (Identify, Get Log Page, Features) | 1.5 | Parsers handle all fixtures; hypothesis tests green |
| 4 | LinuxNvmeAdapter (ioctl primary, nvme-cli fallback) | 2.0 | Unit tests via mocked `fcntl.ioctl`; integration skipped on non-nvme CI |
| 5 | WindowsStorageAdapter (DeviceIoControl via ctypes) | 2.0 | Unit tests via mocked `ctypes.WinDLL`; struct sizes asserted |
| 6 | Test suite: fixtures, parametrization, coverage config | 1.5 | ≥80% coverage on hal/ + adapters/mock.py |
| 7 | Stress harness: fio/diskspd wrappers + profiles + parsers | 2.0 | 3 profiles runnable; output parsed into shared schema |
| 8 | Reporting: HTML template, SMART trend charts, regression | 1.5 | `nvme-sentinel report` produces HTML with embedded charts |
| 9 | CI: full matrix, caching, artifact upload, badge | 1.0 | 6/6 matrix cells green |
| 10 | Docs: README, PlantUML, Design Decisions, setup guide | 1.0 | Diagram renders; doc reviewed |

**Total**: ~15 agent-hours. Realistic real-clock with human review + iteration: 3–5 days.

## 7. Design Decisions (the 3-paragraph deliverable — pre-written)

*This text goes verbatim into `docs/design-decisions.md`. The Phase 10 task just pastes it.*

### 7.1 HAL via `abc.ABC` over `typing.Protocol`

We chose `abc.ABC` with `@abstractmethod` over `typing.Protocol` for `StorageInterface` for three engineering reasons. First, ABCs fail **at import time** when a concrete adapter is missing a method — Protocols fail at the first call site. On a test bench running an overnight regression, fail-fast at import is worth the slight ceremony. Second, ABCs give us a natural home for shared behaviour via a `BaseAdapter` mixin (retry policy, command timing, structured telemetry), so `LinuxNvmeAdapter` and `WindowsStorageAdapter` stay focused on OS-specific ioctls and don't duplicate policy. Third, ABCs serialise better in reviewer conversations: "this method is abstract, every adapter must implement it" maps 1:1 to the UML diagram, where Protocols blur into duck typing.

### 7.2 Raw `ioctl` primary path, `nvme-cli` subprocess fallback

On Linux, the primary path is `ioctl(fd, NVME_IOCTL_ADMIN_CMD, &nvme_passthru_cmd)` via `ctypes` and `fcntl`. This buys three properties the subprocess approach cannot match: (a) **per-command latency drops from tens of milliseconds to microseconds** — decisive when polling SMART sixty times per minute during a 24-hour soak; (b) direct access to the Completion Queue Entry DW0/DW1 result, which `nvme-cli` discards after formatting; (c) no dependence on `nvme-cli` version drift across distros (RHEL 8.x ships 1.x, Ubuntu 24.04 ships 2.x — the JSON schema differs). The `nvme-cli --output-format=json` path is preserved as a **capability-detected fallback** for locked-down benches where `CAP_SYS_ADMIN` or the NVMe passthrough ioctl is denied by seccomp. On Windows the equivalent native path is `DeviceIoControl(IOCTL_STORAGE_PROTOCOL_COMMAND, …)`; no subprocess fallback exists because Windows ships no `nvme-cli` equivalent and vendor tools are not reliably present on test benches.

### 7.3 Mock adapter as a first-class product artefact, not a test fixture

`MockNvmeAdapter` lives in `nvme_sentinel/adapters/`, not under `tests/`. It is a **deterministic byte-accurate simulator** seeded with real captured Identify Controller, Identify Namespace, and SMART Health responses from reference devices. This choice pays off three ways. Operationally, CI runs across six matrix cells without privileged access to any NVMe device — the same tests that protect production behaviour run on a GitHub runner. Architecturally, one mock powers unit tests, integration tests, the `nvme-sentinel demo` CLI command, and local development on machines without NVMe. Most importantly, it enforces honesty in the command parsers: when they consume a real-device byte dump rather than a hand-crafted fixture whose layout the parser author already understands, the first time a contributor's test passes but real hardware fails, the fix lands in the mock and the regression is covered **forever**. This is the shift-left discipline the JD asks for, made concrete in one design decision.

## 8. Quality gates

A PR cannot merge unless **all** of the following hold:
1. `uv run ruff check .` — clean
2. `uv run ruff format --check .` — clean
3. `uv run mypy nvme_sentinel` — clean (strict on `hal/`, `models/`, `commands/`)
4. `uv run pytest -n auto` — all tests pass on all 6 matrix cells
5. `uv run pytest --cov=nvme_sentinel.hal --cov=nvme_sentinel.adapters.mock --cov-fail-under=80` — green
6. `sizeof(NvmePassthruCmd) == 72` and `sizeof(StorageProtocolCommand) == 80` assertions pass at module load
7. No new `# type: ignore` without an inline justification comment
8. No `Any` in any public function signature under `hal/`, `models/`, `commands/`

## 9. Risk register

| # | Risk | Impact | Mitigation |
|---|------|--------|-----------|
| 1 | CI has no real NVMe | Cannot exercise `ioctl` path in CI | Mock adapter; gate real-hardware tests with `@pytest.mark.requires_nvme` and skip in CI |
| 2 | ctypes struct layout drift across OS/Python versions | Silent data corruption | Assert `ctypes.sizeof(…)` at module load; fixture-test with real captured bytes |
| 3 | `fio` / `diskspd` JSON schema changes between versions | Result parser breaks | Pin minimum versions in docs; snapshot-test sample outputs in `tests/fixtures/` |
| 4 | `nvme-cli` output format changes between distros | Fallback breaks | Prefer `--output-format=json`; version-detect before parsing |
| 5 | 128-bit SMART counters | Overflow in other languages | N/A in Python (arbitrary precision `int`); call out in code comment to signal awareness |
| 6 | PlantUML not installed on reviewer's machine | Diagram unreadable | Commit rendered `architecture.svg` alongside `.puml` source |
| 7 | Windows permission model (admin / SYSTEM) for DeviceIoControl | Adapter raises on unprivileged run | Detect + raise a typed `PermissionDenied` exception with actionable message |
| 8 | Path separators / device naming across OS | Cross-platform bugs | `get_adapter()` factory + `Device` abstraction hide it |

## 10. Interview talking points (drill these before the interview)

When walking through the repo, lead with these five:

1. **"The HAL surface is eight methods. Everything else composes."** — shows architectural restraint.
2. **"ioctl first, subprocess fallback — because subprocess latency dominates when polling SMART sixty times per minute during a 24-hour soak."** — shows real-world storage instinct.
3. **"The mock adapter is byte-accurate against a real Identify Controller capture. The same bytes real hardware returns, CI sees too."** — shows first-principles test engineering.
4. **"CI is 2 OS × 3 Python = 6 cells, all green, zero hardware dependency. The matrix guards every merge."** — shows shift-left discipline.
5. **"The Design Decisions doc explains why ABC over Protocol. Happy to walk through that tradeoff."** — invites senior-engineer conversation; signals you think in tradeoffs, not dogma.

If they ask *"how would you extend this for Gen5 / CXL / zoned namespaces?"* — answer:
> *"New CNS values and log pages go into `commands/` as new builder functions; the HAL doesn't change. Zoned namespaces need one extra method on the interface (`zone_mgmt_send/recv`) — that's the test for whether the abstraction is right."*

---

**End of implementation_plan.md.** Next: `task.md` — the atomic, paste-ready prompts for Cursor/Windsurf.