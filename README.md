# nvme-sentinel

> Cross-platform NVMe SSD validation and health inspection using native OS passthrough paths.

---

## What nvme-sentinel is

`nvme-sentinel` is a Python framework that sends NVMe Admin commands through platform-native
interfaces, parses typed protocol responses, and exposes them through a consistent command layer.

The project is optimized for two realities:

- real lab usage on Linux and Windows devices
- CI reliability with zero NVMe hardware, via a deterministic byte-accurate mock adapter

---

## Current implemented scope

### CLI commands

- `nvme-sentinel list-devices` - OS storage inventory + suggested telemetry paths
- `nvme-sentinel info` - Identify Controller summary
- `nvme-sentinel smart` - SMART/Health log read (labels telemetry source), optional HTML output
- `nvme-sentinel collect` - read-only JSON snapshot for baselines and VM host-proxy
- `nvme-sentinel nas discover|collect|report` - Unraid SSH telemetry (NVMe + HDD)
- `nvme-sentinel demo` - end-to-end mock run showing protocol path + generated report

### Platform behavior

- **Linux**: native `ioctl(NVME_IOCTL_ADMIN_CMD)` path (`ctypes` + `fcntl`)
- **Windows**: native `DeviceIoControl(IOCTL_STORAGE_PROTOCOL_COMMAND)` path
- **Windows fallback**: if passthrough is unavailable (permission denied or driver reports
  `ERROR_INVALID_FUNCTION`), the CLI falls back to WMI reliability counters
- **Mock mode**: fixture-backed deterministic adapter used by tests and demo

---

## Architecture at a glance

The HAL keeps a small interface boundary while command parsing and reporting stay shared:

- **HAL**: `StorageInterface` + `BaseAdapter` (timing, retry behavior)
- **Adapters**: `LinuxNvmeAdapter`, `WindowsStorageAdapter`, `MockNvmeAdapter`
- **Commands**: Identify + Log Page command builders
- **Models**: typed parsers for Identify, SMART, Error Log, Firmware Slot
- **Reporting**: HTML output from parsed SMART data

The same command/model code runs against all adapters, which is what makes the project portable.

---

## Technical highlights

- Raw admin passthrough on both OSes, not shell-only wrappers
- Correct Windows IOCTL constant:
  `IOCTL_STORAGE_PROTOCOL_COMMAND = 0x002DD3C8`
- SMART parser handles NVMe 128-bit counters with Python arbitrary precision `int`
- Structured exceptions for capability, permission, protocol status, and device errors
- Strict static/type/test quality gates (`mypy`, `ruff`, `pytest`, coverage gate)

---

## Quickstart

```bash
uv sync
uv run nvme-sentinel demo
```

```bash
# Linux (real device)
uv run nvme-sentinel smart --device /dev/nvme0n1

# Windows (real device, admin shell recommended)
uv run nvme-sentinel smart --device \\.\PhysicalDrive0
```

---

## Validation commands

```bash
uv run mypy nvme_sentinel
uv run pytest tests/unit tests/integration -q
```

Containerized Linux test path:

```bash
docker compose build test
docker compose run --rm test
```

---

## Documentation

- Setup and troubleshooting: [`docs/setup.md`](docs/setup.md)
- Real-hardware test matrix: [`docs/real-hardware-test-matrix.md`](docs/real-hardware-test-matrix.md)
- External Thunderbolt/USB4 bench: [`docs/external-test-bench.md`](docs/external-test-bench.md)
- VM host-proxy workflow: [`docs/vm-host-proxy.md`](docs/vm-host-proxy.md)
- DIY PCB probe concept: [`docs/pcb-probe-concept.md`](docs/pcb-probe-concept.md)
- Design tradeoffs: [`docs/design-decisions.md`](docs/design-decisions.md)
- Long-term observability vision (post-P6): [`docs/VISION.md`](docs/VISION.md)
- Handoff / gated plan: [`MASTER_HANDOFF.md`](MASTER_HANDOFF.md)
- Deep technical overview: [`PROJECT_DESCRIPTION.md`](PROJECT_DESCRIPTION.md)

---

## License

MIT