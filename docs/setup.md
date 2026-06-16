# Setup Guide

## Windows (primary)
### Prerequisites
- Python 3.10–3.12
- [uv](https://astral.sh/uv) — `winget install astral-sh.uv` or `irm https://astral.sh/uv/install.ps1 | iex`
- Docker Desktop (optional, for Linux tests)

### Install
```powershell
git clone https://github.com/your-org/nvme-sentinel
cd nvme-sentinel
uv sync
uv run nvme-sentinel demo
```

### Real NVMe device (Windows, admin PowerShell)
```powershell
# List physical drives
Get-PhysicalDisk | Select-Object DeviceId, FriendlyName, MediaType
# Inventory and SMART
uv run nvme-sentinel list-devices
uv run nvme-sentinel smart --device \\.\PhysicalDrive0
uv run nvme-sentinel collect --device \\.\PhysicalDrive0 --output reports\drive0.json
```

### VM guest (host-proxy snapshot)
```powershell
uv run nvme-sentinel smart --device host-proxy://C:\shared\drive0.json
```

### Run Linux tests via Docker
```powershell
.\scripts\run_linux_tests.ps1 -Build
```

## Linux (AlmaLinux / Ubuntu — WSL2 or bare)
### Prerequisites
```bash
# Ubuntu
sudo apt-get install nvme-cli
curl -LsSf https://astral.sh/uv/install.sh | sh

# AlmaLinux / RHEL
sudo dnf install nvme-cli
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Permissions for /dev/nvme*
```bash
# Option 1: udev rule (persistent)
echo 'SUBSYSTEM=="nvme", KERNEL=="nvme[0-9]*", GROUP="disk", MODE="0660"' \
  | sudo tee /etc/udev/rules.d/99-nvme-sentinel.rules
sudo udevadm control --reload-rules && sudo udevadm trigger

# Option 2: one-shot (for testing)
sudo chmod 660 /dev/nvme0n1
sudo chgrp disk /dev/nvme0n1
```

### Run
```bash
uv sync
uv run nvme-sentinel demo
uv run nvme-sentinel list-devices
uv run nvme-sentinel smart --device /dev/nvme0
uv run nvme-sentinel collect --device /dev/nvme0 --output reports/nvme0.json
```

### Real-hardware docs
- [Test matrix](real-hardware-test-matrix.md)
- [External TB/USB4 bench](external-test-bench.md)
- [VM host-proxy](vm-host-proxy.md)
- [DIY PCB probe concept](pcb-probe-concept.md)

## Docker (any host)
```bash
docker compose run --rm test                       # mock tests, no hardware
docker compose run --rm test pytest tests/ -v -k "not requires_nvme"
```

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `uv run` / `uv sync` Access denied on `.venv\lib64` | Stale or locked virtualenv (common on Windows) | Close terminals/IDE using the venv, then `Remove-Item -Recurse -Force .venv` and `uv sync` |
| `EACCES /dev/nvme0n1` | Missing permissions | Apply udev rule above |
| `ENOTTY: not an NVMe device` | Path is SCSI, not NVMe | Use `/dev/nvme0` not `/dev/sda` |
| `PermissionDenied` on Windows | Not running as admin | Run PowerShell as Administrator |
| `DeviceNotFound \\.\PhysicalDrive0` | Wrong drive index | Check `Get-PhysicalDisk` |
| `CapabilityError: WindowsStorageAdapter requires Windows` | Running on Linux | Use `--mock` or `--device` with Linux path |
| Docker build fails `nvme-cli` | apt mirror issue | Try `docker compose build --no-cache test` |
