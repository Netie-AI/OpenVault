# Real-Hardware Test Matrix

NVME Sentinel validates storage telemetry across several physical and logical paths. All paths are **read-only** by default.

## Matrix

| Target | OS | Device path | Expected telemetry source | Notes |
|--------|-----|-------------|---------------------------|-------|
| Internal SSD | Windows | `\\.\PhysicalDriveN` | `device-io-control` or `wmi` | Admin shell; WMI is degraded subset |
| Internal SSD | Linux | `/dev/nvme0` (controller) | `ioctl` | Prefer controller char dev over `nvme0n1` |
| External TB/USB4 | Windows/Linux | Same as OS enumerates | `native-nvme` / `ioctl` | PCIe tunneling → full NVMe |
| USB NVMe bridge | Any | USB disk path | `usb-bridge-degraded` | Often SCSI/UASP only → `smartctl` |
| Mock / CI | Any | `--mock` | `mock` | Deterministic fixtures |
| VM guest | Any | `host-proxy://path/snap.json` | `host-proxy` | Host runs `collect` first |
| Unraid NAS | Client → SSH | `nas collect --host` | `unraid` | `smartctl` / `nvme-cli` on NAS |

## Commands

```bash
# Inventory
uv run nvme-sentinel list-devices
uv run nvme-sentinel list-devices --json

# SMART with source label
uv run nvme-sentinel smart --device \\.\PhysicalDrive0
uv run nvme-sentinel smart --device /dev/nvme0 --json

# Snapshot for VM / baseline
uv run nvme-sentinel collect --device \\.\PhysicalDrive0 --output reports/host.json

# VM guest (shared folder)
uv run nvme-sentinel smart --device host-proxy:///mnt/shared/host.json

# Unraid
uv run nvme-sentinel nas discover --host tower.local
uv run nvme-sentinel nas collect --host tower.local --output reports/unraid.json
uv run nvme-sentinel nas report --input reports/unraid.json --output reports/unraid.html
```

## Safety

- Do **not** use write/trim/firmware opcodes on boot drives.
- `collect` requires `--readonly-confirmed` (default on).
- Spare SSD + external enclosure recommended for stress testing (future Phase 7).

See also: [external-test-bench.md](external-test-bench.md), [vm-host-proxy.md](vm-host-proxy.md).
