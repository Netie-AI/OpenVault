# nvme-sentinel — Cursor/Windsurf Task Runbook

> Atomic, paste-ready prompts. Each task is self-contained, verifiable, and commits in isolation. Execute sequentially. Do not skip verification — `implementation_plan.md` is the north star; this file is the march order.

---

## 0. How to run this (read once, follow every time)

### One-time human setup

1. **Create the repo shell** (do this *before* opening the agent):
   ```bash
   mkdir nvme-sentinel && cd nvme-sentinel
   git init
   ```

2. **Place these three files at the repo root**:
   - `implementation_plan.md`
   - `task.md` (this file)
   - `.cursorrules` (content below — create it by hand, don't let the agent do it)

3. **Open the repo in Cursor or Windsurf.** Verify:
   - Model set to the strongest available (Claude Opus 4.7 for planning/review, Claude Sonnet 4.7 for iterative edits — in the Cursor "Models" panel, enable both and use Cmd+. to switch per-task)
   - `@Codebase` indexing is on (Settings → Features → Codebase indexing)
   - `.cursorrules` is picked up (Cursor shows it in the bottom status bar)

### `.cursorrules` — create this file manually

```
You are contributing to nvme-sentinel, a cross-platform NVMe SSD validation framework.
Read implementation_plan.md before acting. It is the source of truth.

HARD RULES — enforced on every response:

1. Environment is uv + pyproject.toml. Never invoke pip, poetry, or conda directly.
   Shell commands use `uv run <cmd>` or `uv add <pkg>`. For dev deps: `uv add --dev <pkg>`.
2. Python target: 3.10, 3.11, 3.12. Do not use syntax introduced later (e.g. PEP 695 generics).
3. Type-annotate every public function. mypy --strict must pass on hal/, models/, commands/.
4. No `typing.Any` in public signatures. No unjustified `# type: ignore`.
5. Flat package layout: `nvme_sentinel/` at repo root; tests in `tests/`.
6. Never delete existing tests to make a build pass. If a test is wrong, explain why and ask.
7. Hardware-dependent tests must be marked `@pytest.mark.requires_nvme` with a mock path provided.
8. NVMe field offsets, opcodes, log page IDs: cite implementation_plan.md §4 in code comments.
   Do not invent offsets. If uncertain, stop and ask.
9. Linux ioctl structs: reference kernel uAPI `<linux/nvme_ioctl.h>`. Use ctypes.Structure with
   _pack_ = 1 where the native header uses pragma pack 1. Assert ctypes.sizeof at module load.
10. Windows structs: reference ntddstor.h / storport.h. Same sizeof assertion rule.
11. Before writing code on a task flagged HIGH_RISK, state the file paths you will create or
    modify and wait for my confirmation.

DO NOT:
- Use os.system or subprocess.call without check= and timeout=.
- Introduce a new top-level dependency without declaring it in pyproject.toml AND explaining
  in the response why an existing dep doesn't cover it.
- Rename public APIs silently. If a rename is needed, call it out.
- Write comments that narrate trivial code. Reserve comments for NVMe protocol refs and non-obvious intent.
- Use print() for logging. Use structlog.
```

### The agent invocation loop

For **every task** below:

1. Open a **fresh chat** (Cursor: Cmd+N for new chat). Context bleed between phases is the #1 source of drift.
2. Attach `@implementation_plan.md` and any files the task explicitly touches.
3. Paste the task's **Prompt** block verbatim.
4. When the agent proposes edits, review them **before accepting**. Look especially for: magic numbers without NVMe-spec citations, swallowed exceptions, `Any` in public signatures.
5. Run the task's **Verification** block yourself. Do not accept the task as done until it verifies clean.
6. Commit with the task ID as the commit prefix:
   ```bash
   git add -A
   git commit -m "T2.1: MockNvmeAdapter byte-accurate Identify/SMART"
   ```
7. Only then move to the next task.

### If the agent goes off-rails

- It invents field offsets → paste implementation_plan.md §4.4 into the chat and tell it to start over.
- It uses `pip install` → one-line correction: "Use `uv add`. Re-read `.cursorrules` rule 1."
- It deletes a test → revert the diff; tell it rule 6.
- It produces a 500-line file for a 50-line task → tell it to split; this is an architectural smell.

---

## Phase 0 — Bootstrap (repo + toolchain)

### T0.1 — Repo skeleton, uv, pyproject

**Goal**: Working `uv sync`, empty package importable, pre-commit installed, lint/type/test tools configured.
**Files**: `pyproject.toml`, `.python-version`, `.gitignore`, `README.md`, `nvme_sentinel/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `.pre-commit-config.yaml`
**HIGH_RISK**: NO

**Prompt**:
```
Bootstrap the nvme-sentinel repo per implementation_plan.md §2, §3, §5. Produce all of the
following files, then give me the exact shell commands to initialise locally.

1) pyproject.toml
   - build backend: hatchling
   - [project] name = "nvme-sentinel", version = "0.0.1", requires-python = ">=3.10,<3.13"
   - dependencies: pydantic>=2.5, typer>=0.12, structlog>=24.1, jinja2>=3.1, matplotlib>=3.8
   - [dependency-groups] dev = [pytest>=8, pytest-cov>=5, pytest-xdist>=3, hypothesis>=6.100,
     mypy>=1.10, ruff>=0.6, pre-commit>=3.7, types-jinja2]
   - [tool.hatch.build.targets.wheel] packages = ["nvme_sentinel"]
   - [tool.ruff] line-length = 100, target-version = "py310"
   - [tool.ruff.lint] select = ["E","F","I","N","UP","B","SIM","TID","RUF"]
   - [tool.ruff.format] quote-style = "double"
   - [tool.mypy] strict = true, python_version = "3.10", warn_unused_ignores = true
   - [[tool.mypy.overrides]] module = "tests.*"  → disallow_untyped_defs = false
   - [tool.pytest.ini_options]
     markers = ["requires_nvme: needs a real NVMe device (skipped in CI)",
                "slow: slow test, skipped on fast runs",
                "windows_only: Windows-specific",
                "linux_only: Linux-specific"]
     addopts = "--strict-markers -ra"
     testpaths = ["tests"]
   - [tool.coverage.run] source = ["nvme_sentinel"], branch = true
   - [tool.coverage.report] fail_under = 80, show_missing = true
     exclude_lines = ["pragma: no cover", "if TYPE_CHECKING:", "raise NotImplementedError"]

2) .python-version  →  3.12

3) .gitignore for: Python artefacts, .venv, coverage (htmlcov, .coverage, coverage.xml),
   .ruff_cache, .mypy_cache, .pytest_cache, *.egg-info, build/, dist/, .env, reports/,
   IDE files (.vscode, .idea).

4) .pre-commit-config.yaml with two hooks:
   - ruff (linter + formatter) pinned to ruff-pre-commit v0.6.x
   - mypy (local hook running `uv run mypy nvme_sentinel` on push stage only)

5) nvme_sentinel/__init__.py: `__version__ = "0.0.1"` + `__all__ = ["__version__"]`.

6) tests/__init__.py: empty.

7) tests/conftest.py: one placeholder fixture-free file with module docstring explaining
   that adapter/device fixtures land in Phase 6.

8) README.md:
   - H1: nvme-sentinel
   - One-line tagline: "Cross-platform NVMe SSD validation, characterization, and health monitoring."
   - Quickstart: `uv sync && uv run pytest`
   - Note: "Full documentation in docs/. Architecture diagram in docs/architecture.svg."

Do NOT install anything system-wide. End your response with the exact shell commands I run.
```

**Verification**:
```bash
uv sync
uv run python -c "import nvme_sentinel; print(nvme_sentinel.__version__)"   # → 0.0.1
uv run pytest --collect-only                                                # → 0 tests, no errors
uv run ruff check .                                                         # → clean
uv run ruff format --check .                                                # → clean
uv run mypy nvme_sentinel                                                   # → Success: no issues
uv run pre-commit install                                                   # hooks installed
```

### T0.2 — GitHub Actions CI matrix skeleton

**Goal**: CI workflow triggers on push/PR, runs full matrix, uploads artefacts.
**Files**: `.github/workflows/ci.yml`
**HIGH_RISK**: NO

**Prompt**:
```
Create .github/workflows/ci.yml per implementation_plan.md §6 Phase 9. Requirements:

- Name: "CI"
- Triggers: push to `main`, and all pull_requests
- Matrix: os = [ubuntu-latest, windows-latest], python-version = ["3.10", "3.11", "3.12"].
  fail-fast: false.
- Steps:
  1. actions/checkout@v4
  2. astral-sh/setup-uv@v3 with enable-cache: true
  3. `uv python install ${{ matrix.python-version }}`
  4. `uv sync --all-extras --dev`
  5. `uv run ruff check .`
  6. `uv run ruff format --check .`
  7. `uv run mypy nvme_sentinel`
  8. `uv run pytest -n auto --cov=nvme_sentinel --cov-report=xml --cov-report=term`
  9. Upload coverage.xml as artefact (actions/upload-artifact@v4), named
     `coverage-${{ matrix.os }}-py${{ matrix.python-version }}`
  10. If `reports/` exists, upload it too.

Pin all action versions. Do NOT add codecov integration yet — that's later.
```

**Verification**: `git add . && git commit -m "T0.2: CI matrix skeleton" && git push origin main` — check the Actions tab. All 6 cells must run and pass with 0 collected tests. If a cell fails for env reasons, fix before proceeding.

---

## Phase 1 — HAL contracts & domain models

### T1.1 — Enums and exceptions

**Goal**: Strongly-typed NVMe constants and exception hierarchy.
**Files**: `nvme_sentinel/hal/__init__.py`, `nvme_sentinel/hal/enums.py`, `nvme_sentinel/hal/exceptions.py`
**HIGH_RISK**: NO

**Prompt**:
```
Implement nvme_sentinel/hal/enums.py and nvme_sentinel/hal/exceptions.py per
implementation_plan.md §4.1, §4.2, §4.3, §4.4.

enums.py — use `enum.IntEnum` (values are transmitted to hardware, int semantics matter):
- AdminOpcode: GET_LOG_PAGE=0x02, IDENTIFY=0x06, SET_FEATURES=0x09, GET_FEATURES=0x0A,
  FIRMWARE_COMMIT=0x10, FIRMWARE_DOWNLOAD=0x11
- CNSValue: IDENTIFY_NAMESPACE=0x00, IDENTIFY_CONTROLLER=0x01,
  ACTIVE_NAMESPACE_LIST=0x02, NAMESPACE_ID_DESCRIPTORS=0x03
- LogPageID: ERROR_INFO=0x01, SMART_HEALTH=0x02, FIRMWARE_SLOT=0x03,
  CHANGED_NAMESPACE_LIST=0x04, COMMANDS_SUPPORTED=0x05, PERSISTENT_EVENT_LOG=0x0D

Also an `enum.Flag` named `CriticalWarning` with members matching SMART byte 0 bit layout
from implementation_plan.md §4.4:
  AVAILABLE_SPARE_LOW = 1
  TEMPERATURE_THRESHOLD = 2
  RELIABILITY_DEGRADED = 4
  READ_ONLY = 8
  VOLATILE_BACKUP_FAILED = 16
  PERSISTENT_MEMORY_READONLY = 32

exceptions.py — single inheritance tree:
  NvmeSentinelError(Exception)
  ├── DeviceError(NvmeSentinelError)          # open/close/io at HAL layer
  │   ├── DeviceNotFound(DeviceError)
  │   └── PermissionDenied(DeviceError)
  ├── AdminCommandError(NvmeSentinelError)    # NVMe command returned non-zero status
  │   - attributes: status_code: int, opcode: int, message: str
  │   - __str__ must include hex opcode and status
  ├── ParseError(NvmeSentinelError)           # byte-layout parse failed
  └── CapabilityError(NvmeSentinelError)      # adapter lacks requested feature

All classes have docstrings. hal/__init__.py re-exports everything. Write the import guard
`from __future__ import annotations` at the top of every module in this package.
```

**Verification**:
```bash
uv run python -c "from nvme_sentinel.hal import AdminOpcode, LogPageID, CriticalWarning; \
  assert AdminOpcode.IDENTIFY == 0x06; \
  assert LogPageID.SMART_HEALTH == 0x02; \
  cw = CriticalWarning(0b101); \
  assert CriticalWarning.AVAILABLE_SPARE_LOW in cw and CriticalWarning.RELIABILITY_DEGRADED in cw"
uv run mypy nvme_sentinel
```

### T1.2 — StorageInterface ABC + BaseAdapter

**Goal**: The HAL contract every adapter must honour.
**Files**: `nvme_sentinel/hal/interface.py`, `nvme_sentinel/hal/base.py`
**HIGH_RISK**: NO

**Prompt**:
```
Implement the HAL contract per implementation_plan.md §2 ("thin HAL") and §7.1 ("ABC over
Protocol").

nvme_sentinel/hal/interface.py:

from abc import ABC, abstractmethod

@dataclass(frozen=True, slots=True)
class DeviceInfo:
    path: str
    model: str
    serial: str
    firmware_rev: str
    namespace_count: int
    is_nvme: bool

@dataclass(frozen=True, slots=True)
class AdminCommand:
    opcode: int
    nsid: int = 0
    cdw10: int = 0
    cdw11: int = 0
    cdw12: int = 0
    cdw13: int = 0
    cdw14: int = 0
    cdw15: int = 0
    data_len: int = 0           # bytes to read back from device
    timeout_ms: int = 60_000

@dataclass(frozen=True, slots=True)
class CommandResult:
    status: int                 # 0 on success; NVMe status code otherwise
    result_dw0: int             # Completion Queue Entry DW0
    data: bytes                 # data returned from device (len == command.data_len)

class StorageInterface(ABC):
    """Hardware abstraction for storage devices supporting NVMe admin passthrough."""

    @abstractmethod
    def open(self) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def admin_passthru(self, cmd: AdminCommand) -> CommandResult: ...
    @abstractmethod
    def get_device_info(self) -> DeviceInfo: ...
    @abstractmethod
    def list_namespaces(self) -> list[int]: ...
    @abstractmethod
    def is_nvme(self) -> bool: ...
    @abstractmethod
    def capabilities(self) -> frozenset[str]:
        """Return capability strings, e.g. {'ioctl', 'nvme-cli'}, {'device-io-control'}."""

    def __enter__(self) -> "StorageInterface":
        self.open(); return self
    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

nvme_sentinel/hal/base.py:

Implement BaseAdapter(StorageInterface, ABC): a mixin that provides:
  - structlog-bound logger attribute (`self.log = structlog.get_logger().bind(adapter=...)`)
  - `_timed(self, cmd: AdminCommand)` context manager that logs command duration in ms
  - `_retry(self, fn, retries=2, backoff=0.1)` helper for transient errors (but NOT for
    AdminCommandError — that's a protocol error, not transient)
  - Default `__enter__/__exit__` inherited from StorageInterface

BaseAdapter must remain abstract (do NOT implement admin_passthru / open / close).

Add a TypedDict `TelemetryRecord(total=False)` with keys: opcode, duration_ms, status,
nsid, data_len, adapter — for structured logging.
```

**Verification**:
```bash
uv run python -c "\
from nvme_sentinel.hal.interface import StorageInterface, AdminCommand, DeviceInfo, CommandResult; \
from nvme_sentinel.hal.base import BaseAdapter; \
assert getattr(StorageInterface, '__abstractmethods__'); \
assert 'admin_passthru' in StorageInterface.__abstractmethods__"
uv run mypy nvme_sentinel
```

### T1.3 — Pydantic domain models (Identify, SMART)

**Goal**: Typed models matching real NVMe byte layouts.
**Files**: `nvme_sentinel/models/__init__.py`, `nvme_sentinel/models/smart.py`, `nvme_sentinel/models/identify.py`
**HIGH_RISK**: YES — the SMART parser is the most protocol-sensitive code in the repo.

**Prompt (HIGH_RISK — before writing, state the plan and wait for confirmation)**:
```
Read implementation_plan.md §4.4 carefully. State back to me:
(a) the byte offsets and widths for all u128 counters in SMART,
(b) the endianness, and
(c) your planned file structure and classmethod names.

I will confirm before you write code.

Once confirmed, implement:

nvme_sentinel/models/smart.py — a Pydantic v2 model `SmartHealthLog`:
  - fields match implementation_plan.md §4.4 exactly, same names in snake_case
  - critical_warning: CriticalWarning (the Flag enum from Phase 1)
  - composite_temperature_kelvin: int (raw; derived `composite_temperature_celsius` is a @property)
  - u128 fields are `int` — call this out in a module-level comment referencing Python's
    arbitrary precision ints
  - class method `from_bytes(cls, buf: bytes) -> SmartHealthLog`:
      - raise ParseError if len(buf) != 512
      - cite implementation_plan.md §4.4 in comments for each offset
      - parse little-endian
  - method `to_dict(self) -> dict` for JSON reports

nvme_sentinel/models/identify.py — two Pydantic v2 models:
  - ControllerIdentify: fields vid, ssvid, sn (20 char ASCII, trimmed), mn (40 char ASCII),
    fr (8 char), rab, ieee (3 bytes as hex), cntlid, ver, nn (num namespaces),
    fguid (16 bytes hex), sqes, cqes. Cite offsets from NVMe base spec 2.0c §5.17.2.1.
    Classmethod `from_bytes(buf: bytes) -> ControllerIdentify` — len check 4096.
  - NamespaceIdentify: fields nsze, ncap, nuse, nsfeat, nlbaf, flbas, lbaf (list of
    (metadata_size, lba_data_size_log2, relative_performance) tuples), nguid, eui64.
    Classmethod `from_bytes(buf: bytes, nsid: int) -> NamespaceIdentify` — len check 4096.

Do not implement every field in the 4096-byte Identify structures — pick the subset listed
above and document which offsets are parsed. Leave the rest unparsed.
```

**Verification**:
```bash
uv run python -c "\
from nvme_sentinel.models.smart import SmartHealthLog; \
buf = bytes([0x01]) + (512 - 1) * b'\\x00'; \
buf = buf[:1] + (300).to_bytes(2,'little') + buf[3:]; \
log = SmartHealthLog.from_bytes(buf); \
print('crit:', log.critical_warning, 'temp_K:', log.composite_temperature_kelvin)"
uv run mypy nvme_sentinel
```

---

## Phase 2 — Mock adapter (TDD enabler, product-grade)

### T2.1 — Capture real-device byte fixtures

**Goal**: Generate deterministic binary fixtures that look like real device output.
**Files**: `tests/fixtures/identify_ctrl_generic.bin`, `tests/fixtures/identify_ns.bin`, `tests/fixtures/smart_healthy.bin`, `tests/fixtures/smart_degraded.bin`, plus a `scripts/build_fixtures.py` that generates them.
**HIGH_RISK**: NO (but the fixtures are consumed by every downstream test)

**Prompt**:
```
Create scripts/build_fixtures.py — a tiny script using uv-run-compatible stdlib only
(struct, pathlib) that generates four binary files under tests/fixtures/:

1) identify_ctrl_generic.bin (4096 B): Identify Controller structure populated with
   plausible values: vid=0x144D (Samsung), ssvid=0x144D, sn="NVMESENTINEL0001    "
   (20 B ASCII, space-padded), mn="Generic NVMe SSD Reference              "
   (40 B, space-padded), fr="GS01GR00", nn=1, cntlid=0x0001. Cite each offset.

2) identify_ns.bin (4096 B): Identify Namespace for NSID 1 — nsze=ncap=0x200000 (1 TB at 4k),
   nuse=0x100000 (half full), nlbaf=0, flbas=0, lbaf[0] = (metadata=0, data_size_log2=12,
   rel_perf=0) → 4096-byte LBAs.

3) smart_healthy.bin (512 B): critical_warning=0, composite_temp=313 K (40°C),
   avail_spare=99, avail_spare_threshold=10, percentage_used=3,
   data_units_read=1_000_000, data_units_written=500_000,
   host_read_cmds=10_000_000, host_write_cmds=5_000_000, power_cycles=42,
   power_on_hours=8760 (1 year), unsafe_shutdowns=2, media_errors=0.

4) smart_degraded.bin (512 B): critical_warning=0b00000101 (SPARE_LOW + RELIABILITY),
   composite_temp=358 K (~85°C), avail_spare=8, avail_spare_threshold=10,
   percentage_used=97, media_errors=1234, unsafe_shutdowns=120.

Script should be idempotent (regenerate on demand). Add a Makefile target or a one-liner
in the docstring:  `uv run python scripts/build_fixtures.py`

Run the script as part of this task and commit all four .bin files + the script.
```

**Verification**:
```bash
uv run python scripts/build_fixtures.py
ls -la tests/fixtures/*.bin    # 4 files
uv run python -c "\
from pathlib import Path; \
from nvme_sentinel.models.smart import SmartHealthLog; \
log = SmartHealthLog.from_bytes(Path('tests/fixtures/smart_degraded.bin').read_bytes()); \
assert log.percentage_used == 97; print('OK')"
```

### T2.2 — MockNvmeAdapter

**Goal**: A product-grade deterministic simulator that replays captured fixtures.
**Files**: `nvme_sentinel/adapters/__init__.py`, `nvme_sentinel/adapters/mock.py`, `tests/unit/test_mock_adapter.py`
**HIGH_RISK**: NO

**Prompt**:
```
Implement nvme_sentinel/adapters/mock.py per implementation_plan.md §7.3.

class MockNvmeAdapter(BaseAdapter):
    def __init__(self,
                 device_path: str = "/dev/mock-nvme0",
                 identify_ctrl_path: Path | None = None,
                 identify_ns_path: Path | None = None,
                 smart_path: Path | None = None) -> None:
        """If paths are None, fall back to tests/fixtures/*.bin bundled under
        nvme_sentinel/adapters/_mock_data/ — copy the fixtures there at import time, or
        embed them as package data."""

Behaviour:
- open() sets self._opened = True; close() resets it. admin_passthru raises DeviceError
  if not opened.
- admin_passthru dispatches on opcode + (cdw10 decoded as CNS or LID):
    IDENTIFY (0x06):
      cdw10 low byte == CNSValue.IDENTIFY_CONTROLLER → return 4096 B controller fixture
      cdw10 low byte == CNSValue.IDENTIFY_NAMESPACE  → return 4096 B namespace fixture
      cdw10 low byte == CNSValue.ACTIVE_NAMESPACE_LIST → return bytes with NSID=1 at
        offset 0, rest zeros, total 4096 B
      other CNS → raise AdminCommandError(status=0x0B, opcode=0x06, msg="CNS not mocked")
    GET_LOG_PAGE (0x02):
      cdw10 low byte == LogPageID.SMART_HEALTH → return 512 B smart fixture
      other LID → raise AdminCommandError
    other opcode → raise CapabilityError
- capabilities() returns frozenset({"mock"}).
- is_nvme() returns True.
- list_namespaces() returns [1].
- get_device_info() parses the controller fixture and returns DeviceInfo.

Determinism contract: two separate MockNvmeAdapter instances with the same fixture paths
must produce byte-identical admin_passthru results for the same commands. No random state.

Unit tests in tests/unit/test_mock_adapter.py:
- test open/close state machine
- test admin_passthru IDENTIFY_CONTROLLER returns 4096 B starting with expected VID
- test admin_passthru IDENTIFY_NAMESPACE
- test admin_passthru GET_LOG_PAGE SMART returns 512 B and parses to percentage_used == 3
- test admin_passthru with unknown opcode raises CapabilityError
- test determinism: two instances, same bytes out
- test context manager closes on exit
```

**Verification**:
```bash
uv run pytest tests/unit/test_mock_adapter.py -v
uv run pytest --cov=nvme_sentinel/adapters/mock --cov-report=term-missing \
    tests/unit/test_mock_adapter.py
# Coverage on mock.py ≥ 90%
```

---

## Phase 3 — NVMe command layer

### T3.1 — Identify command builders

**Goal**: Composable functions that build AdminCommand objects and decode responses.
**Files**: `nvme_sentinel/commands/__init__.py`, `nvme_sentinel/commands/identify.py`, `tests/unit/test_commands_identify.py`
**HIGH_RISK**: NO

**Prompt**:
```
Implement nvme_sentinel/commands/identify.py. Every function takes a StorageInterface and
a typed argument; returns a parsed pydantic model.

def identify_controller(device: StorageInterface) -> ControllerIdentify:
    cmd = AdminCommand(
        opcode=AdminOpcode.IDENTIFY,
        cdw10=CNSValue.IDENTIFY_CONTROLLER,  # cdw10 low byte is CNS
        data_len=4096,
    )
    result = device.admin_passthru(cmd)
    if result.status != 0:
        raise AdminCommandError(status=result.status, opcode=int(AdminOpcode.IDENTIFY),
                                message="Identify Controller failed")
    return ControllerIdentify.from_bytes(result.data)

def identify_namespace(device: StorageInterface, nsid: int) -> NamespaceIdentify:
    # nsid must be > 0; raise ValueError if 0
    ...

def active_namespace_list(device: StorageInterface, start_nsid: int = 0) -> list[int]:
    # cdw10 = CNS; cdw1 = start_nsid per spec
    # returns all non-zero NSIDs from 4096-byte response parsed as u32 LE
    ...

Citations in comments: NVMe Base Spec 2.0c §5.17 Identify command.

Tests in tests/unit/test_commands_identify.py use MockNvmeAdapter:
- identify_controller returns ControllerIdentify with vid == 0x144D
- identify_namespace(1) returns model with nsze == 0x200000
- identify_namespace(0) raises ValueError
- active_namespace_list() returns [1]
- when mock raises AdminCommandError, it propagates
```

**Verification**:
```bash
uv run pytest tests/unit/test_commands_identify.py -v
uv run mypy nvme_sentinel
```

### T3.2 — Get Log Page commands (SMART, Error, FW Slot)

**Goal**: Typed builders for the log pages that matter most.
**Files**: `nvme_sentinel/commands/log_pages.py`, `tests/unit/test_commands_log_pages.py`
**HIGH_RISK**: NO

**Prompt**:
```
Implement nvme_sentinel/commands/log_pages.py.

def get_smart_health(device: StorageInterface,
                     nsid: int = 0xFFFFFFFF) -> SmartHealthLog:
    """NSID 0xFFFFFFFF means controller-level SMART. Namespace-scoped SMART requires
    the LPA bit in Identify Controller; we do not check here (caller responsibility),
    but log a warning via structlog if nsid != 0xFFFFFFFF."""
    # cdw10 encoding for Get Log Page:
    #   bits 0-7:   Log Page Identifier (LID)
    #   bits 8-15:  Log Specific Field (LSP)
    #   bits 31-16: NUMDL (Number of DWORDs Lower) — (size_bytes/4) - 1
    # For SMART: 512 B = 128 DW → NUMDL = 127
    cdw10 = LogPageID.SMART_HEALTH | (127 << 16)
    cmd = AdminCommand(opcode=AdminOpcode.GET_LOG_PAGE, nsid=nsid,
                       cdw10=cdw10, data_len=512)
    result = device.admin_passthru(cmd)
    ...
    return SmartHealthLog.from_bytes(result.data)

def get_error_info_log(device: StorageInterface, n_entries: int) -> list[ErrorLogEntry]: ...
def get_firmware_slot_info(device: StorageInterface) -> FirmwareSlotInfo: ...

For ErrorLogEntry and FirmwareSlotInfo: create minimal pydantic models in
nvme_sentinel/models/error_log.py and nvme_sentinel/models/firmware.py. Parse only the
subset needed to demo the feature — document offsets in comments.

Tests use MockNvmeAdapter. Extend mock to serve Error Info (64 B per entry × n zeroed
entries) and FW Slot Info (512 B with afi=0x01, frs1="GS01GR00" + zeros).
```

**Verification**:
```bash
uv run pytest tests/unit/test_commands_log_pages.py -v
uv run mypy nvme_sentinel
```

---

## Phase 4 — Linux adapter (ioctl primary, nvme-cli fallback)

### T4.1 — ctypes struct definitions + ioctl constants

**Goal**: Byte-accurate Python representation of `struct nvme_passthru_cmd`.
**Files**: `nvme_sentinel/adapters/_linux_native.py`
**HIGH_RISK**: YES

**Prompt (HIGH_RISK — state the plan first)**:
```
Read implementation_plan.md §4.5 carefully. Before writing code, confirm to me:
(a) the exact field order and types in struct nvme_passthru_cmd,
(b) the total expected ctypes.sizeof (72 bytes),
(c) how you will compute NVME_IOCTL_ADMIN_CMD value (0xC0484E41 on Linux x86_64),
(d) whether you will use _pack_ = 1 and why.

Once I confirm, implement nvme_sentinel/adapters/_linux_native.py:

- ctypes.Structure subclass `NvmePassthruCmd` with _pack_ = 1 and _fields_ in the exact
  order from §4.5. Use c_uint8, c_uint16, c_uint32, c_uint64 as appropriate.
- Module-level constant `NVME_IOCTL_ADMIN_CMD = 0xC0484E41` (cite: _IOWR('N', 0x41,
  sizeof(nvme_passthru_cmd)=72) → (3<<30) | (72<<16) | (ord('N')<<8) | 0x41 = 0xC0484E41
  verify math in a comment).
- Module-level assertion at import time:
    assert ctypes.sizeof(NvmePassthruCmd) == 72, f"NvmePassthruCmd size {ctypes.sizeof(...)} != 72"
- Helper `build_admin_cmd(cmd: AdminCommand, data_buf: ctypes.Array) -> NvmePassthruCmd`
  that populates the ctypes struct from our AdminCommand dataclass.

No ioctl call yet; that's T4.2. This task is *only* the native types.
```

**Verification**:
```bash
uv run python -c "\
from nvme_sentinel.adapters._linux_native import NvmePassthruCmd, NVME_IOCTL_ADMIN_CMD; \
import ctypes; \
assert ctypes.sizeof(NvmePassthruCmd) == 72; \
assert NVME_IOCTL_ADMIN_CMD == 0xC0484E41; \
print('OK')"
uv run mypy nvme_sentinel
```

### T4.2 — LinuxNvmeAdapter (ioctl path)

**Goal**: The real Linux adapter using `fcntl.ioctl`.
**Files**: `nvme_sentinel/adapters/linux.py`, `tests/unit/test_linux_adapter.py`
**HIGH_RISK**: YES

**Prompt (HIGH_RISK)**:
```
State your plan for mocking fcntl.ioctl in unit tests (monkeypatching vs a thin wrapper
module) before writing code. I confirm, then implement.

nvme_sentinel/adapters/linux.py:

class LinuxNvmeAdapter(BaseAdapter):
    def __init__(self, device_path: str) -> None:
        # device_path like "/dev/nvme0n1" or "/dev/nvme0"
        # store for open(); do not open fd here
    def open(self) -> None:
        # os.open(device_path, os.O_RDWR)
        # raise DeviceNotFound on FileNotFoundError; PermissionDenied on PermissionError
    def close(self) -> None:
        # os.close if opened; idempotent
    def admin_passthru(self, cmd: AdminCommand) -> CommandResult:
        # allocate ctypes buffer of cmd.data_len (or 1 if 0) as c_ubyte array
        # build NvmePassthruCmd, set .addr to ctypes.addressof(buf)
        # call fcntl.ioctl(self._fd, NVME_IOCTL_ADMIN_CMD, native_cmd)
        #   - OSError with errno EACCES/EPERM → PermissionDenied
        #   - OSError with errno ENOTTY → raise CapabilityError("not an NVMe device")
        # status = ioctl return value; if nonzero, raise AdminCommandError
        # return CommandResult(status=0, result_dw0=native_cmd.result, data=bytes(buf))
    def get_device_info(self) -> DeviceInfo:
        # call self via identify_controller() and wrap
    def list_namespaces(self) -> list[int]:
        # call active_namespace_list(self)
    def is_nvme(self) -> bool:
        # return True if the device path starts with /dev/nvme and the identify succeeds
    def capabilities(self) -> frozenset[str]:
        # {"ioctl"} — add "nvme-cli" if shutil.which("nvme") is not None

Wrap fcntl.ioctl behind a module-level callable `_ioctl_call = fcntl.ioctl` so tests can
monkeypatch it. Cite implementation_plan.md §4.5 in a module docstring.

Unit tests in tests/unit/test_linux_adapter.py (marked linux_only, but run with mocked
ioctl so they work on any OS for CI — use sys.platform check with pytest.skip if needed;
prefer monkeypatching to skip-by-platform):
- open() raises DeviceNotFound when path missing
- admin_passthru builds a struct with correct opcode/cdw10
- admin_passthru returns CommandResult.data equal to whatever the mocked ioctl writes
- OSError(EACCES) → PermissionDenied
```

**Verification**:
```bash
uv run pytest tests/unit/test_linux_adapter.py -v
uv run mypy nvme_sentinel
```

### T4.3 — nvme-cli subprocess fallback

**Goal**: Fallback path when ioctl is denied.
**Files**: `nvme_sentinel/adapters/_nvme_cli.py`, extend `nvme_sentinel/adapters/linux.py`
**HIGH_RISK**: NO

**Prompt**:
```
Implement a capability-detected fallback per implementation_plan.md §7.2.

Create nvme_sentinel/adapters/_nvme_cli.py with thin wrappers that invoke
`nvme id-ctrl /dev/... --output-format=json` and parse to bytes (by re-encoding the JSON
fields back into the 4096-byte layout is impractical — so instead, use
`nvme id-ctrl /dev/... --raw-binary` which emits raw bytes to stdout). Use subprocess.run
with check=False, timeout=30, capture_output=True. Raise AdminCommandError on non-zero
return code with stderr in the message.

Commands to wrap:
- id_ctrl_raw(path: str) -> bytes  (4096 B)
- id_ns_raw(path: str, nsid: int) -> bytes
- get_smart_raw(path: str) -> bytes  (512 B)

Extend LinuxNvmeAdapter: if the primary ioctl path raises PermissionDenied or
CapabilityError AND `capabilities()` contains "nvme-cli", retry via the fallback for the
three command types above. Otherwise re-raise.

Log the fallback transition at WARNING level via structlog with a structured event
("nvme_cli_fallback_engaged", reason=...). Unit-test the fallback by monkeypatching
_ioctl_call to raise and subprocess.run to return a fake bytes result.
```

**Verification**:
```bash
uv run pytest tests/unit/test_linux_adapter.py -v -k fallback
uv run mypy nvme_sentinel
```

---

## Phase 5 — Windows adapter (DeviceIoControl)

### T5.1 — ctypes structs for Windows

**Goal**: Byte-accurate `STORAGE_PROTOCOL_COMMAND`.
**Files**: `nvme_sentinel/adapters/_windows_native.py`
**HIGH_RISK**: YES

**Prompt (HIGH_RISK — state the plan first)**:
```
Read implementation_plan.md §4.6. Confirm:
(a) exact _fields_ order and types for STORAGE_PROTOCOL_COMMAND header (80 bytes),
(b) IOCTL_STORAGE_PROTOCOL_COMMAND constant (0x2DD480) — verify derivation with a comment,
(c) how you will lay out the variable-length Command + data buffer trailing the header.

After I confirm, implement nvme_sentinel/adapters/_windows_native.py:

- StorageProtocolCommandHeader (ctypes.Structure, _pack_ = 1) with exact fields from §4.6.
- Assert ctypes.sizeof == 80 at import.
- IOCTL_STORAGE_PROTOCOL_COMMAND = 0x2DD480.
- Enum-like constants: STORAGE_PROTOCOL_TYPE_NVME = 3, STORAGE_PROTOCOL_COMMAND_FLAG_ADAPTER_REQUEST = 0x80000000.
- Helper `build_protocol_command(cmd: AdminCommand) -> bytes`: allocates a bytearray of
  80 + 64 + cmd.data_len, populates the header + 64-byte NVMe command (opcode byte 0, nsid
  bytes 4-7, cdw10-15 at bytes 40..63 per NVMe Submission Queue Entry layout), returns as bytes.

Do NOT call DeviceIoControl yet — this task is only the struct definitions.
```

**Verification**:
```bash
uv run python -c "\
from nvme_sentinel.adapters._windows_native import StorageProtocolCommandHeader, IOCTL_STORAGE_PROTOCOL_COMMAND; \
import ctypes; \
assert ctypes.sizeof(StorageProtocolCommandHeader) == 80; \
assert IOCTL_STORAGE_PROTOCOL_COMMAND == 0x2DD480; \
print('OK')"
uv run mypy nvme_sentinel
```

### T5.2 — WindowsStorageAdapter

**Goal**: The real Windows adapter via `ctypes.WinDLL('kernel32')`.
**Files**: `nvme_sentinel/adapters/windows.py`, `tests/unit/test_windows_adapter.py`
**HIGH_RISK**: YES

**Prompt (HIGH_RISK)**:
```
State your plan for mocking the Win32 API on non-Windows CI (structure the kernel32 calls
behind a module-level `_win32 = _Win32Impl()` object that tests can replace). Confirm,
then implement.

nvme_sentinel/adapters/windows.py:

class WindowsStorageAdapter(BaseAdapter):
    - __init__(device_path: str)  — e.g. "\\\\.\\PhysicalDrive0" or "\\\\.\\Scsi0:"
    - open(): CreateFileW with GENERIC_READ|GENERIC_WRITE, FILE_SHARE_READ|FILE_SHARE_WRITE,
      OPEN_EXISTING. On INVALID_HANDLE_VALUE: check GetLastError → map
      ERROR_FILE_NOT_FOUND → DeviceNotFound, ERROR_ACCESS_DENIED → PermissionDenied.
    - close(): CloseHandle; idempotent.
    - admin_passthru: build_protocol_command → DeviceIoControl(..., IOCTL_STORAGE_PROTOCOL_COMMAND,
      in_buf, in_size, out_buf, out_size, bytes_returned, None). On FALSE return:
      GetLastError → AdminCommandError. Parse the returned buffer: status = header.ReturnStatus;
      data = out_buf[header.DataFromDeviceBufferOffset : that offset + transferred].
    - get_device_info / list_namespaces / is_nvme / capabilities → same pattern as Linux.
    - capabilities() returns frozenset({"device-io-control"}).

Gate the whole module with `if sys.platform != "win32"` early import failure — instead,
keep the module importable on any OS for unit tests, but make `open()` raise
CapabilityError("WindowsStorageAdapter requires Windows") if sys.platform != "win32".

Unit tests marked @pytest.mark.windows_only using the mocked _win32 object so they run
on Linux CI too. Test: open with missing device → DeviceNotFound; admin_passthru builds
correct 64-byte command; admin_passthru parses mocked response correctly.
```

**Verification**:
```bash
uv run pytest tests/unit/test_windows_adapter.py -v
uv run mypy nvme_sentinel
```

### T5.3 — Adapter factory

**Goal**: One function that returns the right adapter for the OS.
**Files**: `nvme_sentinel/hal/factory.py`, `tests/unit/test_factory.py`
**HIGH_RISK**: NO

**Prompt**:
```
Implement nvme_sentinel/hal/factory.py:

def get_adapter(device_path: str | None = None,
                force: Literal["linux", "windows", "mock"] | None = None
                ) -> StorageInterface:
    """
    Return the appropriate StorageInterface.
    - force="mock" → always MockNvmeAdapter
    - force="linux" → LinuxNvmeAdapter (requires device_path)
    - force="windows" → WindowsStorageAdapter (requires device_path)
    - force=None: auto-detect via sys.platform
      * linux  → LinuxNvmeAdapter(device_path)
      * win32  → WindowsStorageAdapter(device_path)
      * other  → MockNvmeAdapter
    - device_path=None when force is None or "mock" → MockNvmeAdapter
    """

Tests: verify each branch. Patch sys.platform for the auto-detect branches.
```

**Verification**:
```bash
uv run pytest tests/unit/test_factory.py -v
uv run mypy nvme_sentinel
```

---

## Phase 6 — Test suite: fixtures, parametrization, coverage gate

### T6.1 — Cross-adapter parametrized fixtures

**Goal**: One test body, three adapters.
**Files**: `tests/conftest.py`, `tests/integration/test_adapter_roundtrip.py`
**HIGH_RISK**: NO

**Prompt**:
```
Rewrite tests/conftest.py to provide the parametrized (adapter, device_path) fixture
required by implementation_plan.md §0 ("Test suite" deliverable):

@pytest.fixture(
    params=[
        pytest.param(("mock", "/dev/mock-nvme0"), id="mock"),
        pytest.param(("linux", "/dev/nvme0n1"),
                     marks=[pytest.mark.requires_nvme, pytest.mark.linux_only],
                     id="linux"),
        pytest.param(("windows", "\\\\.\\PhysicalDrive0"),
                     marks=[pytest.mark.requires_nvme, pytest.mark.windows_only],
                     id="windows"),
    ]
)
def adapter_and_path(request) -> tuple[StorageInterface, str]:
    kind, path = request.param
    ... use factory ...

Add tests/integration/test_adapter_roundtrip.py with parametrized tests that use this
fixture:
- test_open_close
- test_identify_controller_round_trip (calls identify_controller, asserts non-empty model,
  mn is a printable ASCII string)
- test_smart_health_round_trip (calls get_smart_health, asserts temp > 0, percentage_used <= 100)

These tests will mostly skip on CI (only the "mock" param runs without hardware); the
linux/windows params run on real benches.

Also add a pytest_collection_modifyitems hook in conftest.py that automatically skips
items marked requires_nvme when the env var NVME_SENTINEL_REAL_DEVICE is not set.
```

**Verification**:
```bash
uv run pytest tests/integration/test_adapter_roundtrip.py -v
# Expect: mock runs pass; linux/windows skip with "requires real device"
```

### T6.2 — Coverage gate on HAL

**Goal**: Enforce ≥80% on HAL and mock adapter.
**Files**: update `pyproject.toml` + add a `scripts/check_hal_coverage.sh` (Linux) and `.ps1` (Windows)
**HIGH_RISK**: NO

**Prompt**:
```
Tighten the coverage configuration so CI fails if HAL or MockNvmeAdapter drop below 80%.

1) In pyproject.toml, refine [tool.coverage.run]:
     source = ["nvme_sentinel.hal", "nvme_sentinel.adapters.mock", "nvme_sentinel.commands"]
     branch = true
   Keep fail_under = 80.

2) Add a composite pytest invocation to CI that runs twice:
   (a) full suite with cov on whole package (informational) — already in T0.2.
   (b) a targeted run after (a):
       uv run pytest --cov=nvme_sentinel.hal --cov=nvme_sentinel.adapters.mock \
                     --cov-fail-under=80 --cov-report=term-missing tests/unit tests/integration

   Update .github/workflows/ci.yml to add step "HAL coverage gate" running that command.

3) Add scripts/check_hal_coverage.sh and .ps1 for local developers.

Do not lower any existing coverage thresholds.
```

**Verification**:
```bash
uv run pytest --cov=nvme_sentinel.hal --cov=nvme_sentinel.adapters.mock --cov-fail-under=80
# Exit code 0 required
```

### T6.3 — Hypothesis property tests on SMART parser

**Goal**: Catch parser edge cases the author didn't think of.
**Files**: `tests/unit/test_smart_properties.py`
**HIGH_RISK**: NO

**Prompt**:
```
Add hypothesis property tests for SmartHealthLog.from_bytes:

from hypothesis import given, strategies as st, settings

@given(st.binary(min_size=512, max_size=512))
@settings(max_examples=500, deadline=None)
def test_smart_parser_never_crashes_on_arbitrary_512_bytes(buf: bytes) -> None:
    try:
        log = SmartHealthLog.from_bytes(buf)
    except ParseError:
        return  # acceptable
    # If parse succeeded, invariants must hold:
    assert 0 <= log.available_spare <= 100  # spec says 0-100; real devices may overflow,
                                            # but we clip — if not, document and remove

@given(st.binary().filter(lambda b: len(b) != 512))
def test_smart_parser_rejects_wrong_length(buf: bytes) -> None:
    with pytest.raises(ParseError):
        SmartHealthLog.from_bytes(buf)

@given(st.integers(min_value=0, max_value=63))
def test_critical_warning_flag_roundtrip(val: int) -> None:
    ...

If the first test fails on a real edge case (e.g. percentage_used > 100), either clip in
the parser and document, or tighten the assertion and file a bug — tell me which.
```

**Verification**:
```bash
uv run pytest tests/unit/test_smart_properties.py -v
```

---

## Phase 7 — Stress harness (fio / diskspd)

### T7.1 — Job profiles

**Files**: `nvme_sentinel/stress/__init__.py`, `nvme_sentinel/stress/profiles.py`

**Prompt**:
```
Implement nvme_sentinel/stress/profiles.py:

@dataclass(frozen=True, slots=True)
class JobProfile:
    name: str
    rw: Literal["read","write","randread","randwrite","randrw","rw"]
    block_size_kb: int
    io_depth: int
    num_jobs: int
    duration_sec: int
    read_percent: int | None = None     # for randrw only
    direct: bool = True

Pre-defined profiles (export as module-level constants):
- SEQ_READ_128K_QD16  = JobProfile("seq_read", "read", 128, 16, 1, 60)
- SEQ_WRITE_128K_QD16 = JobProfile("seq_write", "write", 128, 16, 1, 60)
- RAND_READ_4K_QD32   = JobProfile("rand_read_4k", "randread", 4, 32, 4, 60)
- RAND_WRITE_4K_QD1   = JobProfile("rand_write_4k_qd1", "randwrite", 4, 1, 1, 60)
- MIXED_70_30_4K_QD32 = JobProfile("mixed_70_30_4k", "randrw", 4, 32, 4, 60, read_percent=70)
- ENDURANCE_PRECOND   = JobProfile("endurance_precond", "randwrite", 128, 32, 4, 7200)

Add a list `STANDARD_PROFILES = [...]` of the first 5. Add a docstring on the module
pointing out that these mirror JESD-style enterprise SSD characterization workloads
(4K rand read QD32 for IOPS, 4K rand write QD1 for latency, 128K seq for bandwidth,
70/30 for OLTP-like).
```

**Verification**: import test, dataclass frozen invariant.

### T7.2 — Fio runner + parser

**Files**: `nvme_sentinel/stress/fio.py`, `tests/unit/test_fio_parser.py`, `tests/fixtures/fio_output_sample.json`

**Prompt**:
```
Implement nvme_sentinel/stress/fio.py:

class FioRunner:
    def __init__(self, fio_binary: str = "fio") -> None: ...
    def ensure_available(self) -> None:
        # shutil.which(fio_binary); raise CapabilityError if missing
    def run(self, device_path: str, profile: JobProfile,
            output_dir: Path) -> FioResult: ...
        # Build arg list:
        #   fio --name={profile.name} --filename={device_path}
        #       --rw={profile.rw} --bs={profile.block_size_kb}k
        #       --iodepth={profile.io_depth} --numjobs={profile.num_jobs}
        #       --time_based --runtime={profile.duration_sec}
        #       --direct={1 if profile.direct else 0}
        #       --ioengine=libaio  (Linux) or --ioengine=windowsaio  (Windows)
        #       --group_reporting --output-format=json
        #       --output={output_dir}/{profile.name}.json
        # If randrw: add --rwmixread={profile.read_percent}
        # subprocess.run with check=True, timeout=profile.duration_sec + 60
        # Parse the JSON output into FioResult

@dataclass(frozen=True, slots=True)
class FioResult:
    profile_name: str
    read_iops: float
    write_iops: float
    read_bw_mib_s: float
    write_bw_mib_s: float
    read_lat_ns_p50: float
    read_lat_ns_p99: float
    read_lat_ns_p99_99: float
    write_lat_ns_p50: float
    write_lat_ns_p99: float
    write_lat_ns_p99_99: float
    total_errors: int
    raw: dict[str, Any]    # full JSON for audit

Parser reads fio JSON (top-level 'jobs'[0], with 'read' and 'write' sub-dicts containing
'iops', 'bw' (KiB/s → convert), 'clat_ns' (with percentiles)).

Commit a realistic tests/fixtures/fio_output_sample.json from `fio --name=demo
--filename=/dev/null --rw=randread --bs=4k --iodepth=32 --time_based --runtime=2
--ioengine=null --output-format=json` (generate it; if no fio locally, write a plausible
one by hand and document it in the fixture header).

Tests: parse_fio_json returns expected FioResult fields; handles missing percentiles.
```

**Verification**:
```bash
uv run pytest tests/unit/test_fio_parser.py -v
```

### T7.3 — Diskspd runner + parser

**Files**: `nvme_sentinel/stress/diskspd.py`, `tests/unit/test_diskspd_parser.py`, `tests/fixtures/diskspd_output_sample.xml`

**Prompt**:
```
Implement DiskspdRunner analogously. Key facts:
- diskspd emits XML when invoked with -Rxml
- Args: -b<bs>K -d<duration> -t<numjobs> -o<iodepth> -r (random) -w<write%> -L (latency)
  -c<size> (for file targets) -Z1G (initialize buffer) -Sh (disable caching)
- Mapping JobProfile → diskspd args:
    SEQ_READ:  -b128K -w0 (no -r)
    RAND_READ: -b4K -r -w0
    MIXED_70_30: -b4K -r -w30
- Parse XML: /Results/TimeSpan/Thread/Target → AverageReadBytesPerSecond,
  AverageReadIopsPerSecond; /Results/TimeSpan/Latency/Bucket[@percentile='99.99']

Same FioResult-shaped output (shared dataclass — promote FioResult to
`nvme_sentinel/stress/parser.py` as `StressResult` so both runners produce the same type).
```

**Verification**:
```bash
uv run pytest tests/unit/test_diskspd_parser.py -v
```

---

## Phase 8 — Reporting (HTML + SMART trend charts)

### T8.1 — SMART trend charts

**Files**: `nvme_sentinel/reporting/__init__.py`, `nvme_sentinel/reporting/charts.py`, `tests/unit/test_charts.py`

**Prompt**:
```
Implement nvme_sentinel/reporting/charts.py using matplotlib (Agg backend — set at
module top: matplotlib.use('Agg') before any pyplot import, since CI has no display).

Functions:

def smart_trend_figure(samples: list[tuple[datetime, SmartHealthLog]]) -> Figure:
    """Returns a matplotlib Figure with 2x2 subplots:
    (0,0) composite temperature vs time (line)
    (0,1) percentage_used vs time (line) + available_spare on twin axis
    (1,0) data_units_read and data_units_written vs time (stacked)
    (1,1) critical_warning as heatmap-like timeline of non-zero events
    """

def fig_to_base64_png(fig: Figure) -> str:
    """Render to PNG, base64-encode, return as data: URI for embedding in HTML."""

Tests: synthesize 24 samples over 24 hours, verify figure has 4 axes, no exceptions.
```

**Verification**:
```bash
uv run pytest tests/unit/test_charts.py -v
```

### T8.2 — HTML regression report

**Files**: `nvme_sentinel/reporting/html.py`, `nvme_sentinel/reporting/trends.py`, `nvme_sentinel/reporting/_templates/report.html.j2`, `tests/unit/test_html_report.py`

**Prompt**:
```
Implement a Jinja2-based HTML report:

nvme_sentinel/reporting/trends.py:
- def compare_to_baseline(current: SmartHealthLog, baseline: SmartHealthLog,
                          thresholds: dict[str, float] | None = None) -> list[Regression]
  where Regression = dataclass(metric: str, baseline: float, current: float, pct_delta: float, severity: Literal["info","warn","critical"])
  Default thresholds: percentage_used +5pt → critical; composite_temp +10K → warn;
  media_errors nonzero delta → critical; available_spare -5pt → warn.

nvme_sentinel/reporting/html.py:
- class ReportBuilder:
    def add_device(device_info: DeviceInfo)
    def add_smart_trend(samples: list[tuple[datetime, SmartHealthLog]])
    def add_stress_result(result: StressResult)
    def add_regression(regressions: list[Regression])
    def render() -> str   # full HTML
    def save(path: Path) -> None

Template (report.html.j2) under nvme_sentinel/reporting/_templates/. Include the base64
SMART trend chart inline. Use minimal embedded CSS. No external network dependencies
(all CSS/JS inline — this is an offline test bench artefact). Ensure the template loader
uses PackageLoader('nvme_sentinel.reporting', '_templates').

Test: generate a report from synthetic data, parse the resulting HTML with stdlib
html.parser to assert expected sections present.
```

**Verification**:
```bash
uv run pytest tests/unit/test_html_report.py -v
uv run python -c "\
from datetime import datetime, timedelta; \
from nvme_sentinel.reporting.html import ReportBuilder; \
from nvme_sentinel.models.smart import SmartHealthLog; \
from pathlib import Path; \
buf = Path('tests/fixtures/smart_healthy.bin').read_bytes(); \
log = SmartHealthLog.from_bytes(buf); \
rb = ReportBuilder(); \
rb.add_smart_trend([(datetime.now() - timedelta(hours=i), log) for i in range(24)]); \
rb.save(Path('reports/demo.html')); \
print('OK → reports/demo.html')"
```

---

## Phase 9 — CLI + CI hardening

### T9.1 — Typer CLI

**Files**: `nvme_sentinel/cli.py`, `tests/unit/test_cli.py`; update `pyproject.toml` `[project.scripts]`

**Prompt**:
```
Implement nvme_sentinel/cli.py with Typer. Commands:

  nvme-sentinel info [--device PATH] [--mock]
      → prints DeviceInfo as a table (rich if available, else plain)

  nvme-sentinel smart [--device PATH] [--mock] [--json]
      → prints SMART health; --json emits pydantic .model_dump_json()

  nvme-sentinel stress [--device PATH] [--mock] [--profile NAME]
                       [--output-dir reports/]
      → runs one JobProfile (default: RAND_READ_4K_QD32), prints summary
      → --profile list shows available names

  nvme-sentinel report [--smart-log PATH]... [--output PATH]
      → builds the HTML report from one or more captured SMART dumps

  nvme-sentinel demo
      → e2e using MockNvmeAdapter: info + smart + stress (with fake fio output)
        + report. Useful for interview demos — runs end-to-end in < 5 seconds.

Wire up via [project.scripts] in pyproject.toml:
  nvme-sentinel = "nvme_sentinel.cli:app"

Tests use typer.testing.CliRunner; assert exit code 0 and expected substrings.
```

**Verification**:
```bash
uv sync
uv run nvme-sentinel --help
uv run nvme-sentinel demo
uv run pytest tests/unit/test_cli.py -v
```

### T9.2 — CI polish: badges, caching, artefact upload

**Files**: `.github/workflows/ci.yml`, `README.md`

**Prompt**:
```
Harden .github/workflows/ci.yml:

1) Add job-level concurrency: cancel in-progress runs on same branch
     concurrency:
       group: ci-${{ github.ref }}
       cancel-in-progress: true

2) Split into two jobs:
   - `lint` (ubuntu-latest, python 3.12 only): ruff + mypy. Runs first.
   - `test` (the matrix): needs: lint. Runs pytest + coverage gate.

3) After the matrix, a third job `coverage-report` that downloads all coverage artefacts
   and produces a combined coverage.xml (coverage combine). Do not integrate with
   codecov yet; just archive the combined XML.

4) Cache uv's global cache via setup-uv@v3 (already does this; confirm).

5) Add a CI badge to README.md at the top.

Do not weaken any existing gate.
```

**Verification**: push to a branch, open PR, observe all jobs green in GH Actions.

---

## Phase 10 — Docs, PlantUML, Design Decisions

> **Session notes (apply before / while executing T10.x)**  
> 1. **Demo logging noise** — `admin_command_timing` and other structlog lines must not interleave with `nvme-sentinel demo` during a live interview. The implementation reconfigures structlog at the start of `demo()` to a discard sink (`io.StringIO`); do not regress to printing timing on the terminal during demo.  
> 2. **SMART offsets 192–199** — Per NVMe Base, these are *Warning Composite Temperature Time* and *Critical Composite Temperature Time* (minutes), not thermal-management transition counts. The model/report use `warning_composite_temp_time_minutes` and `critical_composite_temp_time_minutes` (`SmartHealthLog.to_dict()` feeds the HTML report — wrong labels will show up in front of an interviewer).

### T10.1 — PlantUML architecture diagram

**Files**: `docs/architecture.puml`, `docs/architecture.svg`

**Prompt**:
```
Create docs/architecture.puml — a PlantUML class diagram mirroring implementation_plan.md §2:

- abstract class StorageInterface with methods: open(), close(), admin_passthru(),
  get_device_info(), list_namespaces(), is_nvme(), capabilities()
- BaseAdapter (abstract) extending StorageInterface
- LinuxNvmeAdapter extending BaseAdapter
- WindowsStorageAdapter extending BaseAdapter
- MockNvmeAdapter extending BaseAdapter
- Dependency arrows from "commands" package (classes identify_controller, get_smart_health,
  active_namespace_list) INTO StorageInterface
- Dependency arrows from StressRunner (FioRunner, DiskspdRunner) to StorageInterface
- Dependency from ReportBuilder to SmartHealthLog, StressResult

Use skinparam monochrome false; classFontStyle plain; arrowThickness 1.

Then render it: if `plantuml` JAR is available, produce architecture.svg; otherwise use
an online render URL in docs/setup.md explaining how a reviewer renders it.

CRITICAL: commit the rendered SVG alongside the .puml source, per implementation_plan.md
§9 risk #6.
```

**Verification**:
```bash
test -f docs/architecture.puml
test -f docs/architecture.svg
# Open the SVG in a browser; verify it renders.
```

### T10.2 — Design Decisions doc + README + setup guide

**Files**: `docs/design-decisions.md`, `docs/setup.md`, `README.md`

**Prompt**:
```
Create three documentation files.

1) docs/design-decisions.md — paste verbatim the three paragraphs from
   implementation_plan.md §7 (§7.1, §7.2, §7.3). Add a preamble linking to
   architecture.svg and a closing "Future extensions" section listing:
   - Zoned Namespaces (adds zone_mgmt_send/recv to the HAL)
   - NVMe over TCP/Fabrics adapters
   - CXL Type-3 memory device integration
   - Real-time SMART-to-Prometheus exporter

2) docs/setup.md — Linux and Windows and WSL2 setup guides, covering:
   - uv install (astral.sh/uv)
   - `uv sync`
   - `uv run pre-commit install`
   - Running demo: `uv run nvme-sentinel demo`
   - (Linux) nvme-cli install + udev permissions for /dev/nvme0n1
   - (Windows) Admin shell requirement for DeviceIoControl on PhysicalDrive
   - Troubleshooting section: EACCES on /dev/nvme*, "not an NVMe device" errors

3) Expand README.md to enterprise quality:
   - Badges: CI, Python versions, license
   - One-paragraph elevator pitch
   - Architecture diagram (embed docs/architecture.svg)
   - Quickstart
   - Feature list
   - Link to docs/design-decisions.md and docs/setup.md
   - "Interview walkthrough" section pointing at the five talking points from
     implementation_plan.md §10

Keep README under 200 lines; link out for depth.
```

**Verification**:
```bash
# No code verification — read the three files and confirm they match the spec.
# Open README.md in a GitHub preview to verify rendering.
```

---

## Completion checklist (run this before declaring done)

Run each command locally and verify all pass:

```bash
# 1. Environment
uv sync

# 2. Hygiene
uv run ruff check .
uv run ruff format --check .
uv run mypy nvme_sentinel

# 3. Tests + coverage
uv run pytest -n auto --cov=nvme_sentinel --cov-report=term
uv run pytest --cov=nvme_sentinel.hal --cov=nvme_sentinel.adapters.mock --cov-fail-under=80

# 4. CLI demo
uv run nvme-sentinel demo

# 5. Report rendering
uv run nvme-sentinel report --smart-log tests/fixtures/smart_healthy.bin \
    --output reports/demo.html && test -s reports/demo.html

# 6. CI: push, verify 6/6 matrix cells pass + artefacts uploaded.

# 7. Docs
test -f docs/architecture.puml
test -f docs/architecture.svg
test -f docs/design-decisions.md
test -f docs/setup.md
grep -q "ABC over Protocol" docs/design-decisions.md
```

**If every line above succeeds, the project is interview-ready.**

---

## Interview presentation flow (the 12-minute walkthrough)

When demoing live:

1. **0:00–0:30** — Open `docs/architecture.svg`. Say the HAL surface is 8 methods.
2. **0:30–2:00** — Walk `nvme_sentinel/hal/interface.py`. Point out `AdminCommand` dataclass and how it maps to the NVMe Submission Queue Entry.
3. **2:00–4:00** — Show `nvme_sentinel/adapters/linux.py`. Highlight the ioctl constant and the `NvmePassthruCmd` ctypes struct with the `sizeof == 72` assertion. Say: *"subprocess latency dominates during soak testing; ioctl removes it."*
4. **4:00–5:30** — Show `nvme_sentinel/adapters/mock.py` and the 3-paragraph Design Decisions doc's §7.3. Say: *"the mock is byte-accurate against real captures — the same bytes CI sees, real hardware returns."*
5. **5:30–7:00** — Run `uv run nvme-sentinel demo`. Walk through the output live.
6. **7:00–8:30** — Open `reports/demo.html` showing SMART trend charts. Say: *"this is what a PM or customer sees after a nightly regression."*
7. **8:30–10:00** — Open GH Actions tab. Show 6/6 matrix cells green. Say: *"shift-left without hardware."*
8. **10:00–11:30** — Open `docs/design-decisions.md`. Walk the three paragraphs.
9. **11:30–12:00** — "How would I extend this for Zoned Namespaces / CXL?" — answer as in implementation_plan.md §10.

---

**End of task.md.** Execute sequentially. Commit after every verified task. Don't skip HIGH_RISK state-the-plan gates — they are where hallucinations die.