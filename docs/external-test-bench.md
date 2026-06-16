# External Thunderbolt / USB4 NVMe Test Bench

## Goal

Provide a **spare** NVMe SSD in an enclosure so NVME Sentinel can exercise full NVMe admin passthrough without touching the OS drive.

## Recommended hardware

| Item | Purpose |
|------|---------|
| Spare M.2 NVMe SSD (not boot) | Device under test |
| TB3/TB4 or USB4 NVMe enclosure | PCIe tunneling when possible |
| USB-C cable (40 Gbps rated for TB) | Stable link |
| Optional heatsink/fan | Avoid thermal throttle during long reads |

## Enclosure classes

### A — Thunderbolt / USB4 (preferred)

- Host often sees a real **NVMe** controller.
- Windows: `\\.\PhysicalDriveN` + `device-io-control`.
- Linux: `/dev/nvme0` + `ioctl`.

Verify:

```bash
uv run nvme-sentinel list-devices
uv run nvme-sentinel smart --device <path> --json
```

Expect telemetry source: `device-io-control` or `ioctl`, not `wmi` alone.

### B — USB-only bridge (degraded)

- Bridge may expose **SCSI/UASP** only; native NVMe admin blocked.
- Telemetry source: `usb-bridge-degraded` or WMI/smartctl subset.
- Use `smartctl -j` on Linux as supplemental read (not yet wired in CLI).

## Workflow

1. Install SSD in enclosure; connect to laptop.
2. `list-devices` — note `bus_type` and `is_nvme`.
3. `smart --device …` — confirm full 512-byte SMART fields.
4. `collect --device … --output reports/external.json` — baseline snapshot.
5. Re-run after soak tests (when stress harness lands).

## DIY PCB

For a custom carrier board (bridge module + M.2 socket), see [pcb-probe-concept.md](pcb-probe-concept.md).
