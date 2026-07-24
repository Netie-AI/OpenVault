"""Tier — gated hardware remediation (GPU / CPU / fan)."""

from __future__ import annotations

from openmw.openvault.control.actions import run_control_action
from openmw.openvault.control.capabilities import probe_control_capabilities

__all__ = ["probe_control_capabilities", "run_control_action"]
