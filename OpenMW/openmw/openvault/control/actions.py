"""Control action dispatcher with dry_run default + audit log."""

from __future__ import annotations

import json
import time
from typing import Any

from openmw.openvault.control import cpu as cpu_mod
from openmw.openvault.control import fans as fans_mod
from openmw.openvault.control import gpu as gpu_mod
from openmw.openvault.control.capabilities import probe_control_capabilities
from openmw.openvault.paths import ensure_home


def _audit(entry: dict[str, Any]) -> None:
    path = ensure_home() / "control_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True) + "\n")


def run_control_action(
    action: str,
    *,
    dry_run: bool = True,
    confirm: bool = False,
    percent: int = 50,
) -> dict[str, Any]:
    """Dispatch a control action. Writes require confirm=True and dry_run=False."""
    caps = probe_control_capabilities()
    result: dict[str, Any]
    if action == "gpu.status":
        result = gpu_mod.gpu_status()
    elif action == "gpu.power_hint":
        result = gpu_mod.gpu_power_hint(confirm=confirm, dry_run=dry_run)
    elif action == "cpu.hint":
        result = cpu_mod.cpu_hint(confirm=confirm, dry_run=dry_run)
    elif action == "fan.set":
        result = fans_mod.fan_set(percent=percent, confirm=confirm, dry_run=dry_run)
    else:
        result = {"ok": False, "capability": False, "error": f"unknown action: {action}"}

    entry = {
        "ts": time.time(),
        "action": action,
        "dry_run": dry_run,
        "confirm": confirm,
        "result_ok": bool(result.get("ok")),
        "capability": result.get("capability"),
        "error": result.get("error"),
    }
    _audit(entry)
    return {
        "action": action,
        "dry_run": dry_run,
        "confirm": confirm,
        "capabilities": caps,
        "result": result,
    }
