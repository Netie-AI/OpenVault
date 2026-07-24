"""Lightweight LAN device discovery for Small Software cloud.

Uses local interfaces + ARP table (Windows/Linux). No aggressive port scans.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class LanDevice:
    ip: str
    mac: str
    hostname: str
    iface: str
    kind: str  # self | peer | gateway | unknown
    reachable: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _local_ips() -> list[tuple[str, str]]:
    """Return (ip, iface_hint) for non-loopback IPv4 addresses."""
    found: list[tuple[str, str]] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = str(info[4][0])
            if ip.startswith("127."):
                continue
            found.append((ip, "primary"))
    except OSError:
        pass
    # Also try UDP connect trick for default route IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127.") and (ip, "route") not in found:
            found.append((ip, "route"))
    except OSError:
        pass
    return found


def _arp_rows() -> list[tuple[str, str]]:
    """Parse ARP table → (ip, mac)."""
    rows: list[tuple[str, str]] = []
    try:
        if os.name == "nt":
            out = subprocess.check_output(["arp", "-a"], text=True, errors="ignore", timeout=8)
            for line in out.splitlines():
                m = re.search(
                    r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F\-]{17}|\S+)\s+\w+",
                    line,
                )
                if m:
                    rows.append((m.group(1), m.group(2).replace("-", ":").lower()))
        else:
            out = subprocess.check_output(["arp", "-an"], text=True, errors="ignore", timeout=8)
            for line in out.splitlines():
                m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-fA-F:]{11,17})", line)
                if m:
                    rows.append((m.group(1), m.group(2).lower()))
    except (OSError, subprocess.SubprocessError):
        pass
    return rows


def _hostname(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


def discover_lan_devices(*, include_self: bool = True) -> dict[str, Any]:
    """Discover LAN peers from ARP + local interfaces."""
    t0 = time.time()
    local = _local_ips()
    local_set = {ip for ip, _ in local}
    devices: list[LanDevice] = []

    if include_self:
        for ip, iface in local:
            devices.append(
                LanDevice(
                    ip=ip,
                    mac="",
                    hostname=socket.gethostname(),
                    iface=iface,
                    kind="self",
                    reachable=True,
                )
            )

    for ip, mac in _arp_rows():
        if ip in local_set or ip.endswith(".255") or ip.endswith(".0"):
            continue
        kind = "gateway" if ip.endswith(".1") else "peer"
        devices.append(
            LanDevice(
                ip=ip,
                mac=mac,
                hostname=_hostname(ip),
                iface="arp",
                kind=kind,
                reachable=True,
            )
        )

    # Deduplicate by IP
    seen: set[str] = set()
    unique: list[LanDevice] = []
    for d in devices:
        if d.ip in seen:
            continue
        seen.add(d.ip)
        unique.append(d)

    return {
        "ok": True,
        "count": len(unique),
        "devices": [d.to_dict() for d in unique],
        "policy": "private-LAN-only · no public internet share",
        "elapsed_ms": int((time.time() - t0) * 1000),
    }
