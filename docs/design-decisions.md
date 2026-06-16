# Design Decisions

> Architecture diagram: [architecture.svg](architecture.svg). Core tradeoffs: **ABC over Protocol** for `StorageInterface` (§7.1).

### 7.1 HAL via `abc.ABC` over `typing.Protocol`

We chose `abc.ABC` with `@abstractmethod` over `typing.Protocol` for `StorageInterface` for three engineering reasons. First, ABCs fail **at import time** when a concrete adapter is missing a method — Protocols fail at the first call site. On a test bench running an overnight regression, fail-fast at import is worth the slight ceremony. Second, ABCs give us a natural home for shared behaviour via a `BaseAdapter` mixin (retry policy, command timing, structured telemetry), so `LinuxNvmeAdapter` and `WindowsStorageAdapter` stay focused on OS-specific ioctls and don't duplicate policy. Third, ABCs serialise better in reviewer conversations: "this method is abstract, every adapter must implement it" maps 1:1 to the UML diagram, where Protocols blur into duck typing.

### 7.2 Raw `ioctl` primary path, `nvme-cli` subprocess fallback

On Linux, the primary path is `ioctl(fd, NVME_IOCTL_ADMIN_CMD, &nvme_passthru_cmd)` via `ctypes` and `fcntl`. This buys three properties the subprocess approach cannot match: (a) **per-command latency drops from tens of milliseconds to microseconds** — decisive when polling SMART sixty times per minute during a 24-hour soak; (b) direct access to the Completion Queue Entry DW0/DW1 result, which `nvme-cli` discards after formatting; (c) no dependence on `nvme-cli` version drift across distros (RHEL 8.x ships 1.x, Ubuntu 24.04 ships 2.x — the JSON schema differs). The `nvme-cli --output-format=json` path is preserved as a **capability-detected fallback** for locked-down benches where `CAP_SYS_ADMIN` or the NVMe passthrough ioctl is denied by seccomp. On Windows the equivalent native path is `DeviceIoControl(IOCTL_STORAGE_PROTOCOL_COMMAND, …)`; no subprocess fallback exists because Windows ships no `nvme-cli` equivalent and vendor tools are not reliably present on test benches.

### 7.3 Mock adapter as a first-class product artefact, not a test fixture

`MockNvmeAdapter` lives in `nvme_sentinel/adapters/`, not under `tests/`. It is a **deterministic byte-accurate simulator** seeded with real captured Identify Controller, Identify Namespace, and SMART Health responses from reference devices. This choice pays off three ways. Operationally, CI runs across six matrix cells without privileged access to any NVMe device — the same tests that protect production behaviour run on a GitHub runner. Architecturally, one mock powers unit tests, integration tests, the `nvme-sentinel demo` CLI command, and local development on machines without NVMe. Most importantly, it enforces honesty in the command parsers: when they consume a real-device byte dump rather than a hand-crafted fixture whose layout the parser author already understands, the first time a contributor's test passes but real hardware fails, the fix lands in the mock and the regression is covered **forever**. This is the shift-left discipline the JD asks for, made concrete in one design decision.

## Future Extensions

| Extension | HAL change |
|-----------|------------|
| Zoned Namespaces (ZNS) | Add `zone_mgmt_send/recv` to StorageInterface — tests whether the abstraction is right |
| NVMe over TCP/Fabrics | New adapter class; HAL unchanged |
| CXL Type-3 memory | New adapter; commands layer extended for CXL Management Component Transport |
| SMART → Prometheus exporter | New reporting module; HAL unchanged |
| Multi-device parallel stress | `asyncio.gather` over per-device adapters; factory unchanged |
