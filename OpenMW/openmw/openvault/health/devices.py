"""Laptop inventory / device health cards for the OpenVault console."""

from __future__ import annotations

from typing import Any

from openmw.demo_payload import build_demo_payload


def devices_payload(
    *,
    use_live_detect: bool = True,
    model_id: str = "llama-3.3-8b",
) -> dict[str, Any]:
    """Return detection + device health cards (mock-safe)."""
    return build_demo_payload(model_id=model_id, use_live_detect=use_live_detect)
