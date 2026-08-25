"""AWS path — same OpenVault server plan as VPS, plus SSM restart.

Does not call AWS APIs. Emits the checklist and the SSM send-command shape
that `ship.server` already puts on the ServerPlan.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AwsRenderPlan:
    hostname: str
    steps: list[str] = field(default_factory=list)
    warning: str = (
        "OpenVault owns HTTP (Caddy + systemd). This does not call AWS APIs; "
        "set OPENVAULT_SHIP_MODE=live and vps_host to apply on the instance."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_aws_render_plan(*, hostname: str = "") -> AwsRenderPlan:
    host = hostname or "app.example.com"
    return AwsRenderPlan(
        hostname=host,
        steps=[
            f"Provision (or reuse) an EC2/VPS with SSH or SSM for {host}",
            "Install Caddy; OpenVault emits the Caddyfile (TLS + reverse_proxy)",
            "Install the systemd unit OpenVault emits; Restart=on-failure",
            "Point the A record at the instance; Caddy obtains Let's Encrypt",
            "Health: curl -fsS https://{host}/healthz",
            "Restart without SSH: aws ssm send-command AWS-RunShellScript "
            "(see ServerPlan.ssm_restart)",
            "Vercel is not used. OpenVault is the HTTP runtime.",
        ],
    )
