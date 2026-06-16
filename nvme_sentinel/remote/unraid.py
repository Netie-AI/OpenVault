"""Unraid NAS telemetry via SSH (smartctl / nvme-cli / lsblk)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from nvme_sentinel.telemetry.source import TelemetrySource


class UnraidDiskHealth(BaseModel):
    """Health record for one disk on Unraid."""

    model_config = ConfigDict(frozen=True)

    name: str
    device_path: str
    transport: str = ""
    is_nvme: bool = False
    model: str = ""
    serial: str = ""
    size_bytes: int | None = None
    telemetry_source: TelemetrySource = TelemetrySource.SMARTCTL
    smart_json: dict[str, object] | None = None
    nvme_smart_json: dict[str, object] | None = None
    pool: str = ""
    role: str = ""


class UnraidSnapshot(BaseModel):
    """Full Unraid inventory + SMART collection."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    host: str
    readonly: bool = True
    lsblk: dict[str, object] | None = None
    disks: list[UnraidDiskHealth] = Field(default_factory=list)
    notes: str | None = None


def _ssh_run(
    host: str,
    command: str,
    *,
    user: str = "root",
    port: int = 22,
    identity_file: str | None = None,
    timeout: int = 60,
) -> tuple[int, str, str]:
    target = f"{user}@{host}"
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port)]
    if identity_file:
        cmd.extend(["-i", identity_file])
    cmd.extend([target, command])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def discover_unraid_disks(
    host: str,
    *,
    user: str = "root",
    port: int = 22,
    identity_file: str | None = None,
) -> list[UnraidDiskHealth]:
    """List block devices on Unraid via remote lsblk."""
    code, out, _ = _ssh_run(
        host,
        "lsblk -J -o NAME,SIZE,TYPE,MODEL,SERIAL,TRAN,MOUNTPOINT",
        user=user,
        port=port,
        identity_file=identity_file,
    )
    if code != 0 or not out.strip():
        return []

    data = json.loads(out)
    disks: list[UnraidDiskHealth] = []
    blockdevices = data.get("blockdevices")
    if not isinstance(blockdevices, list):
        return disks

    for node in blockdevices:
        if not isinstance(node, dict) or node.get("type") != "disk":
            continue
        name = str(node.get("name") or "")
        tran = str(node.get("tran") or "").lower()
        is_nvme = name.startswith("nvme") or tran == "nvme"
        mount = str(node.get("mountpoint") or "")
        role = ""
        pool = ""
        if "/mnt/cache" in mount or "cache" in name.lower():
            role = "cache"
        elif mount.startswith("/mnt/disk") or mount.startswith("/mnt/user"):
            role = "array"

        disks.append(
            UnraidDiskHealth(
                name=name,
                device_path=f"/dev/{name}",
                transport=tran,
                is_nvme=is_nvme,
                model=str(node.get("model") or "").strip(),
                serial=str(node.get("serial") or "").strip(),
                telemetry_source=(
                    TelemetrySource.NVME_CLI if is_nvme else TelemetrySource.SMARTCTL
                ),
                pool=pool,
                role=role,
            )
        )
    return disks


def _collect_disk_smart(
    host: str,
    disk: UnraidDiskHealth,
    *,
    user: str = "root",
    port: int = 22,
    identity_file: str | None = None,
) -> UnraidDiskHealth:
    dev = disk.device_path
    if disk.is_nvme:
        nvme_dev = dev
        if "n" in dev.split("/")[-1]:
            base = dev.split("n")[0]
            nvme_dev = base if base.startswith("/dev/") else f"/dev/{base.split('/')[-1]}"
        cmd = f"nvme smart-log {nvme_dev} --output-format=json 2>/dev/null || true"
        code, out, _ = _ssh_run(host, cmd, user=user, port=port, identity_file=identity_file)
        nvme_json: dict[str, object] | None = None
        if code == 0 and out.strip():
            try:
                parsed = json.loads(out)
                if isinstance(parsed, dict):
                    nvme_json = parsed
            except json.JSONDecodeError:
                nvme_json = None
        return disk.model_copy(
            update={
                "nvme_smart_json": nvme_json,
                "telemetry_source": TelemetrySource.NVME_CLI,
            }
        )

    cmd = f"smartctl -j -a {dev} 2>/dev/null || true"
    code, out, _ = _ssh_run(host, cmd, user=user, port=port, identity_file=identity_file)
    smart_json: dict[str, object] | None = None
    if code == 0 and out.strip():
        try:
            parsed = json.loads(out)
            if isinstance(parsed, dict):
                smart_json = parsed
        except json.JSONDecodeError:
            smart_json = None
    return disk.model_copy(
        update={
            "smart_json": smart_json,
            "telemetry_source": TelemetrySource.SMARTCTL,
        }
    )


def collect_unraid_snapshot(
    host: str,
    *,
    user: str = "root",
    port: int = 22,
    identity_file: str | None = None,
    include_smart: bool = True,
) -> UnraidSnapshot:
    """Discover disks and optionally collect SMART for each (read-only)."""
    code, out, err = _ssh_run(
        host,
        "lsblk -J -o NAME,SIZE,TYPE,MODEL,SERIAL,TRAN,MOUNTPOINT",
        user=user,
        port=port,
        identity_file=identity_file,
    )
    lsblk_data: dict[str, object] | None = None
    if code == 0 and out.strip():
        try:
            parsed = json.loads(out)
            if isinstance(parsed, dict):
                lsblk_data = parsed
        except json.JSONDecodeError:
            lsblk_data = None

    disks = discover_unraid_disks(host, user=user, port=port, identity_file=identity_file)
    if include_smart:
        disks = [
            _collect_disk_smart(host, d, user=user, port=port, identity_file=identity_file)
            for d in disks
        ]

    notes = None if code == 0 else f"lsblk failed: {err.strip()}"
    return UnraidSnapshot(
        collected_at=datetime.now(timezone.utc),
        host=host,
        lsblk=lsblk_data,
        disks=disks,
        notes=notes,
    )
