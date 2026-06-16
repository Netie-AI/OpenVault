#!/usr/bin/env python3
"""Optional real-hardware smoke test: Identify + SMART, with WMI fallback (Windows)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


@dataclass
class HarnessResult:
    status: str
    detail: str


def _run_harness(device: str) -> HarnessResult:
    from nvme_sentinel.commands.identify import identify_controller
    from nvme_sentinel.commands.log_pages import get_smart_health
    from nvme_sentinel.hal.factory import get_adapter

    try:
        with get_adapter(device_path=device) as dev:
            identify_controller(dev)
            get_smart_health(dev)
    except Exception as exc:
        return HarnessResult("FAIL", str(exc))
    return HarnessResult("OK", "Identify Controller + SMART Health Log succeeded")


def _print_wmi_counters(device_path: str) -> None:
    from nvme_sentinel.adapters._wmi_fallback import disk_number_from_path, get_reliability_counters

    dn = disk_number_from_path(device_path)
    counters = get_reliability_counters(dn)
    print("\n  [WMI MSFT_StorageReliabilityCounter — limited fields, no admin]")
    for k, v in counters.items():
        if v is not None:
            print(f"  {k:<40} {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description="nvme-sentinel real device smoke test")
    parser.add_argument(
        "--device",
        default=None,
        help=r"Device path (default: \\.\PhysicalDrive0 on Windows, /dev/nvme0n1 on Linux)",
    )
    parser.add_argument(
        "--wmi",
        action="store_true",
        help="Always query WMI storage reliability counters (Windows)",
    )
    args = parser.parse_args()
    device = args.device
    if device is None:
        device = r"\\.\PhysicalDrive0" if sys.platform == "win32" else "/dev/nvme0n1"

    result = _run_harness(device)
    do_wmi = args.wmi or (result.status == "FAIL" and "permission" in result.detail.lower())

    if do_wmi:
        if sys.platform != "win32":
            print("[WARN] WMI fallback is only available on Windows.")
            if result.status != "OK":
                print(f"[FAIL] {result.detail}")
                raise SystemExit(1)
            print(f"[OK] {result.detail}")
            raise SystemExit(0)
        print("[WARN] Running WMI MSFT_StorageReliabilityCounter fallback...")
        try:
            _print_wmi_counters(device)
        except Exception as exc:
            print(f"[FAIL] WMI fallback failed: {exc}")
            if result.status != "OK":
                print(f"       Original error: {result.detail}")
            raise SystemExit(1) from exc
        if result.status != "OK":
            print("\n[WARN] NVMe passthrough failed; WMI counters shown above are partial.")
            raise SystemExit(0)
        print("\n[OK] Passthrough succeeded; WMI counters above are supplemental.")

    if result.status != "OK":
        print(f"[FAIL] {result.detail}")
        raise SystemExit(1)
    print(f"[OK] {result.detail}")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
