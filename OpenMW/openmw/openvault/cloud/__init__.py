"""Small Software LAN cloud — share company apps like a Google Doc on the LAN.

Inspired by Pete Koomen (Cloud for Small Software) + Aaron Epstein (Multiplayer AI).
Scope v0: loopback + private LAN only; OpenVault is the allow authority.
"""

from __future__ import annotations

from openmw.openvault.cloud.firewall import evaluate_action, list_rules
from openmw.openvault.cloud.lan_discover import discover_lan_devices
from openmw.openvault.cloud.multiplayer import (
    create_session,
    get_session,
    join_session,
    list_sessions,
    post_event,
)
from openmw.openvault.cloud.share_store import ShareStore

__all__ = [
    "ShareStore",
    "create_session",
    "discover_lan_devices",
    "evaluate_action",
    "get_session",
    "join_session",
    "list_rules",
    "list_sessions",
    "post_event",
]
