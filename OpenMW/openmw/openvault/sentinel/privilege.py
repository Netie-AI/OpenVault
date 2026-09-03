"""Process privilege probe — NVMe admin passthrough needs a raw device handle."""

from __future__ import annotations

import ctypes
import os
import sys


def is_elevated() -> bool:
    """True when this process can open a raw block device (Administrator / root)."""
    if sys.platform == "win32":
        # getattr keeps this importable (and type-checkable) on non-Windows hosts.
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return False
        try:
            return bool(windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    return bool(geteuid() == 0)


def elevation_hint() -> str:
    """Platform-specific instruction for obtaining the missing privilege."""
    if sys.platform == "win32":
        return "restart OpenVault elevated: Start-Process openvault -Verb RunAs"
    return "run as root, or grant CAP_SYS_ADMIN on the OpenVault process"
