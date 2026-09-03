"""Execution firewall / extruder — warn and hard-block rule bypass.

OpenVault alone allows leave-machine / remote share / deploy. Any attempt to
skip the gate returns allowed=False with a WARN reason (never silent allow).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Action = Literal[
    "run_local",
    "share_lan",
    "join_session",
    "deploy",
    "leave_machine",
    "remote_exec",
    "bypass_gate",
    "export_secrets",
    "open_public_internet",
]

# Permanent deny list — cannot be overridden by client flags.
HARD_DENY: frozenset[str] = frozenset(
    {
        "bypass_gate",
        "export_secrets",
        "remote_exec",
        "open_public_internet",
    }
)


@dataclass
class FirewallDecision:
    allowed: bool
    action: str
    level: Literal["allow", "warn", "deny"]
    reasons: list[str] = field(default_factory=list)
    rules_applied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_rules() -> list[dict[str, str]]:
    return [
        {
            "id": "lan-only-share",
            "summary": "Share apps only on private LAN / loopback, never public internet.",
        },
        {
            "id": "no-secret-export",
            "summary": "Secrets never leave OpenVault; share packs carry refs only.",
        },
        {
            "id": "gate-required",
            "summary": "Deploy / leave-machine require /api/gate/check allow.",
        },
        {
            "id": "no-bypass",
            "summary": "Client flags like bypass=1 / force=1 are denied with WARN.",
        },
        {
            "id": "no-remote-exec",
            "summary": "Peers may pull shared apps; they cannot push shell to this host.",
        },
    ]


def evaluate_action(
    action: str,
    *,
    destination: str = "",
    peer_ip: str = "",
    client_flags: dict[str, Any] | None = None,
    gate_allowed: bool | None = None,
) -> FirewallDecision:
    """Evaluate an action. Bypass attempts always deny + warn."""
    action = (action or "").strip().lower()
    flags = dict(client_flags or {})
    reasons: list[str] = []
    applied: list[str] = []

    # Explicit bypass / force — never honor
    bypass_keys = ("bypass", "bypass_gate", "force", "skip_rules", "ignore_gate")
    if any(bool(flags.get(k)) for k in bypass_keys):
        return FirewallDecision(
            allowed=False,
            action=action or "unknown",
            level="deny",
            reasons=[
                "WARN: execution-rule bypass attempted — blocked. "
                "Cannot skip OpenVault gate/firewall."
            ],
            rules_applied=["no-bypass"],
        )

    if action in HARD_DENY or action == "bypass_gate":
        return FirewallDecision(
            allowed=False,
            action=action,
            level="deny",
            reasons=[f"WARN: action '{action}' is permanently denied by OpenVault firewall."],
            rules_applied=["no-bypass", "no-secret-export", "no-remote-exec"],
        )

    if action not in (
        "run_local",
        "share_lan",
        "join_session",
        "deploy",
        "leave_machine",
        "remote_exec",
        "export_secrets",
        "open_public_internet",
    ):
        return FirewallDecision(
            allowed=False,
            action=action or "unknown",
            level="deny",
            reasons=[f"WARN: unknown action '{action}' — blocked."],
            rules_applied=["gate-required"],
        )

    dest = (destination or "").lower()
    peer = (peer_ip or "").strip()

    if action in ("deploy", "leave_machine"):
        applied.append("gate-required")
        if gate_allowed is not True:
            return FirewallDecision(
                allowed=False,
                action=action,
                level="deny",
                reasons=[
                    "WARN: deploy/leave requires OpenVault gate allow "
                    "(POST /api/gate/check). Cannot proceed."
                ],
                rules_applied=applied,
            )

    if action == "share_lan":
        applied.append("lan-only-share")
        # Allow only private / loopback URLs
        if (dest.startswith("http://") or dest.startswith("https://")) and not _is_private_url(
            dest
        ):
            return FirewallDecision(
                allowed=False,
                action=action,
                level="deny",
                reasons=["WARN: share destination is not private LAN/loopback — blocked."],
                rules_applied=applied,
            )
        if peer and not _is_private_ip(peer):
            return FirewallDecision(
                allowed=False,
                action=action,
                level="deny",
                reasons=[f"WARN: peer {peer} is not on private LAN — blocked."],
                rules_applied=applied,
            )

    if action == "join_session":
        applied.append("lan-only-share")
        if peer and not _is_private_ip(peer):
            return FirewallDecision(
                allowed=False,
                action=action,
                level="deny",
                reasons=[f"WARN: join from non-LAN peer {peer} — blocked."],
                rules_applied=applied,
            )

    return FirewallDecision(
        allowed=True,
        action=action,
        level="allow",
        reasons=reasons or ["allowed under Small Software LAN rules"],
        rules_applied=applied or ["lan-only-share"],
    )


def _is_private_ip(ip: str) -> bool:
    ip = ip.split("%")[0].strip()
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if a == 10:
        return True
    if a == 192 and b == 168:
        return True
    return a == 172 and 16 <= b <= 31


def _is_private_url(url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    return _is_private_ip(host)
