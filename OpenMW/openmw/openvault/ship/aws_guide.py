"""AWS / Render checklist — guide only, never auto-applies cloud credentials."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AwsRenderPlan:
    hostname: str
    steps: list[str] = field(default_factory=list)
    warning: str = "Guide only — OpenVault does not call AWS APIs."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_aws_render_plan(*, hostname: str = "") -> AwsRenderPlan:
    host = hostname or "app.example.com"
    return AwsRenderPlan(
        hostname=host,
        steps=[
            f"Create a public load balancer / Render web service for {host}",
            "Terminate TLS at the edge (ACM or Render cert)",
            "Point A/CNAME at the LB",
            "Prefer Cursor Origin + Vercel for Next.js/static instead of AWS",
        ],
    )
