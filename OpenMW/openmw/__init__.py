"""Open measurement workspace for KV-offload + profiler correlation."""

from __future__ import annotations

from typing import Any

__all__ = ["OffloadRunResult", "run_offload_measurement_loop"]
__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    # Console/FreeRoute/one-seat demo must import without numpy. Offload stays lazy.
    if name in __all__:
        from openmw.run import OffloadRunResult, run_offload_measurement_loop

        mapping = {
            "OffloadRunResult": OffloadRunResult,
            "run_offload_measurement_loop": run_offload_measurement_loop,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
