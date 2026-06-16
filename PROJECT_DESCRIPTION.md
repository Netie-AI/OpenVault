# NVMe Sentinel: Project Description and Technical Deep Dive

## 1) What NVMe Sentinel is

`nvme-sentinel` is a **host-side cross-platform NVMe validation and wear-accounting framework**:

- sends NVMe Admin commands through native OS paths (Linux ioctl, Windows DeviceIoControl)
- parses protocol responses into typed Python models
- produces read-only snapshots, HTML reports, and (P6) wear-delta bench artifacts

It supports real hardware benches and CI without NVMe hardware via a deterministic mock adapter.

## 2) Why this project exists

Most SSD validation tooling breaks on platform boundaries, privileged hardware access, or
parser logic coupled to one transport. NVMe Sentinel separates:

- **transport** (adapters)
- **protocol** (commands + models)
- **presentation** (CLI, reporting, `BenchRunReport`)

## 3) Core architecture

### HAL boundary

`StorageInterface`: `open`, `close`, `admin_passthru`, `get_device_info`, `list_namespaces`,
`is_nvme`, `capabilities`. `BaseAdapter` provides timing and retry policy.

### Adapters

- **LinuxNvmeAdapter** — `NVME_IOCTL_ADMIN_CMD` via ctypes + fcntl; nvme-cli fallback (P2)
- **WindowsStorageAdapter** — `IOCTL_STORAGE_PROTOCOL_COMMAND`; WMI fallback in CLI
- **MockNvmeAdapter** — fixture-backed replay for CI
- **HostProxyAdapter** — replays `DeviceSnapshot` JSON from host-collected baselines

### CLI commands (current)

- `list-devices` — OS storage inventory
- `info` — Identify Controller summary
- `smart` — SMART/Health log + optional HTML
- `collect` — read-only JSON snapshot (`DeviceSnapshot`)
- `nas discover|collect|report` — Unraid SSH telemetry
- `demo` — mock end-to-end run

### Product direction (P6)

`BenchRunReport`: `DeviceSnapshot(before)` + `StressResult` + `DeviceSnapshot(after)` +
TBW/wear delta + environment manifest. See [`MASTER_HANDOFF.md`](MASTER_HANDOFF.md).

Long-term observability vision: [`docs/VISION.md`](docs/VISION.md) (post-P6 only).

## 4) Notable technical details

- Linux `NvmePassthruCmd` sizeof assertion == 72 at import
- Windows `STORAGE_PROTOCOL_COMMAND` header sizeof == 80; IOCTL `0x002DD3C8`
- SMART 128-bit counters via Python arbitrary-precision `int`
- SMART bytes 192–199: `warning_composite_temp_time_minutes`, `critical_composite_temp_time_minutes`
  (NVMe Base Spec 2.0c §6.1.3 Table 188)
- CI: 2 OS × 3 Python; coverage gate 80% on HAL + mock

## 5) Sequenced work remaining

P1 → P6 per [`MASTER_HANDOFF.md`](MASTER_HANDOFF.md): audit tests, nvme-cli fallback,
parametrized integration, stress harness, wear-delta report.

## 6) Bottom line

NVMe Sentinel is a credible cross-platform validation foundation. The adoption differentiator
is reproducible wear accounting: clone the repo, run the bench, verify TBW delta on your own drive.
