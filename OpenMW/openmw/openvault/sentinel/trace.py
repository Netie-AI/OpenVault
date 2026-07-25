"""Capture real NVMe admin-command timings for the bottleneck path trace.

``nvme_sentinel.hal.base.BaseAdapter._timed`` already emits one structlog event
per admin command (``admin_command_timing`` with opcode, nsid, data_len, status,
duration_ms, adapter). Nothing ever listened. This module listens: it prepends a
capture processor to the structlog chain, drives a fixed read-only command
sequence, and hands the records to ``nvme_profiler.fuse``.

Ordering is load-bearing. ``BaseAdapter.__init__`` calls
``structlog.get_logger().bind(...)``, which freezes the processor chain that was
configured at that moment — so the adapter must be constructed *inside* the
capture window or its events bypass us entirely.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import structlog

from nvme_sentinel.commands.identify import active_namespace_list, identify_controller
from nvme_sentinel.commands.log_pages import (
    get_error_info_log,
    get_firmware_slot_info,
    get_smart_health,
)
from nvme_sentinel.hal.exceptions import NvmeSentinelError

from openmw.openvault.sentinel.engine import classify_failure, open_device

TIMING_EVENT = "admin_command_timing"

# structlog configuration is process-global, so only one trace runs at a time.
_CAPTURE_LOCK = threading.Lock()


@dataclass(slots=True)
class TraceResult:
    """Outcome of one admin-command trace run."""

    records: list[dict[str, Any]] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    adapter: str | None = None
    ok: bool = False
    error: str | None = None
    needs_admin: bool = False


@contextmanager
def capture_admin_timings() -> Iterator[list[dict[str, Any]]]:
    """Collect ``admin_command_timing`` events emitted while the block runs."""
    captured: list[dict[str, Any]] = []

    def _capture(
        _logger: Any,
        _method: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        if event_dict.get("event") == TIMING_EVENT:
            captured.append(dict(event_dict))
        return event_dict

    with _CAPTURE_LOCK:
        saved = structlog.get_config()
        structlog.configure(processors=[_capture, *saved["processors"]])
        try:
            yield captured
        finally:
            structlog.configure(**saved)


def run_admin_trace(device_path: str, *, mock: bool) -> TraceResult:
    """Issue Identify + SMART + log-page reads, timing each admin command."""
    result = TraceResult()
    with capture_admin_timings() as captured:
        try:
            with open_device(device_path, mock=mock) as dev:
                result.adapter = type(dev).__name__

                identify_controller(dev)
                result.commands.append("identify_controller")

                # Each of these is a separate admin command, so each contributes
                # its own hop; a one-command trace has nothing to compare against.
                for label, call in (
                    ("active_namespace_list", lambda: active_namespace_list(dev)),
                    ("smart_health_log", lambda: get_smart_health(dev)),
                    ("firmware_slot_log", lambda: get_firmware_slot_info(dev)),
                    ("error_info_log", lambda: get_error_info_log(dev, 1)),
                ):
                    try:
                        call()
                        result.commands.append(label)
                    except NvmeSentinelError:
                        # Optional log pages are vendor-dependent; a controller
                        # that refuses one still gives a usable timeline.
                        continue
            result.ok = True
        except (NvmeSentinelError, OSError) as exc:
            failure = classify_failure(exc, device_path)
            result.error = failure.reason
            result.needs_admin = failure.needs_admin

    result.records = captured
    if result.ok and not result.records:
        result.ok = False
        result.error = (
            "adapter ran but emitted no admin_command_timing events — "
            "structlog capture did not bind"
        )
    return result
