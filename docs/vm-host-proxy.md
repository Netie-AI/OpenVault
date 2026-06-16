# VM and Host-Proxy Workflow

Use NVME Sentinel on the **host** where the SSD is attached, then analyze the same telemetry inside a **VM** via read-only JSON snapshots.

## Why host-proxy first

- Boot SSD cannot be PCIe-passthrough to a VM while Windows runs on it.
- Admin passthrough on the host is simpler than duplicating drivers in the guest.
- Snapshots are safe to copy over shared folders (read-only JSON).

## Host (Windows or Linux)

```bash
# On host — enumerate
uv run nvme-sentinel list-devices

# Collect read-only snapshot
uv run nvme-sentinel collect --device \\.\PhysicalDrive1 --output reports/drive1.json
# Linux example:
# uv run nvme-sentinel collect --device /dev/nvme0 --output reports/nvme0.json
```

Copy `reports/drive1.json` into the VM shared folder (e.g. `/mnt/host-share/drive1.json`).

## Guest (VM)

```bash
uv run nvme-sentinel smart --device host-proxy:///mnt/host-share/drive1.json
uv run nvme-sentinel info --device host-proxy:///mnt/host-share/drive1.json
```

Or use a plain path if the file ends with `.json`:

```bash
uv run nvme-sentinel smart --device /mnt/host-share/drive1.json
```

Telemetry source label: **`host-proxy`**.

## Snapshot requirements for full replay

Host `collect` should include:

- `identify_controller_b64` (4096 bytes)
- `smart_health_b64` (512 bytes)

Without these, `info`/`smart` may fail with `CapabilityError` — re-run collect on the host with `--mock` off and working passthrough.

## Optional: PCIe passthrough (advanced)

- **Spare non-boot NVMe only**
- Enable VT-d/IOMMU; pass entire NVMe controller to VM
- Guest runs `smart --device /dev/nvme0` natively
- Not documented as automated; lab-specific

## Read-only guarantee

`collect` uses only Identify + Get Log Page (SMART). No format, trim, or firmware commands.
