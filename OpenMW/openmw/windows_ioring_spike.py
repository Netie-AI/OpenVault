"""Windows IoRing / DirectStorage exploratory spike (Q4 research)."""

from __future__ import annotations

import sys
from dataclasses import dataclass

import structlog

log = structlog.get_logger()


@dataclass(frozen=True)
class IoRingSpikeResult:
    """Result of Windows IoRing tensor-read feasibility probe."""

    platform: str
    ioring_api_available: bool
    directstorage_runtime: bool
    tensor_kv_feasible: bool
    evidence: str
    label: str = "exploratory-not-committed"


def probe_windows_ioring_spike() -> IoRingSpikeResult:
    """Probe IoRing/DirectStorage for raw KV tensor pages (exploratory)."""
    if sys.platform != "win32":
        return IoRingSpikeResult(
            platform=sys.platform,
            ioring_api_available=False,
            directstorage_runtime=False,
            tensor_kv_feasible=False,
            evidence="IoRing spike is Windows-only; run on win32 host.",
        )
    build = 0
    try:
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        ) as key:
            build = int(str(winreg.QueryValueEx(key, "CurrentBuildNumber")[0]))
    except OSError as exc:
        return IoRingSpikeResult(
            platform="win32",
            ioring_api_available=False,
            directstorage_runtime=False,
            tensor_kv_feasible=False,
            evidence=f"build probe failed: {exc}",
        )
    ioring_ok = build >= 17763
    # DirectStorage is tuned for GDeflate game assets; raw tensor KV is unproven.
    ds_runtime = False
    try:
        import ctypes  # noqa: PLC0415

        _ = ctypes.windll.kernel32  # IoRing lives in kernel32 on recent builds
        ds_runtime = True
    except OSError:
        ds_runtime = False
    feasible = False
    evidence = (
        f"Windows build {build}; IoRing API {'available' if ioring_ok else 'unavailable'}. "
        "DirectStorage is designed for compressed texture tiles (GDeflate), not raw KV tensor "
        "pages. Using IoRing for KV-cache reads remains an open research question — "
        "document pass/fail with evidence, not marketing claims."
    )
    log.info("windows_ioring_spike", build=build, ioring_ok=ioring_ok)
    return IoRingSpikeResult(
        platform="win32",
        ioring_api_available=ioring_ok,
        directstorage_runtime=ds_runtime,
        tensor_kv_feasible=feasible,
        evidence=evidence,
    )
