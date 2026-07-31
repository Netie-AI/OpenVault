"""One-stop cloud targets: FreeBuild Cloud, any SSH VPS, AWS teach+budget.

FreeBuild itself does not provision AWS ELB/S3/Lambda — it is Render-like via
Oblien Cloud + Docker on SSH servers. OpenVault adds:
  - target picker (same mental model as FreeBuild DeployTargetStep)
  - monthly bill cap (OpenVault-enforced before ship)
  - AWS resource plan as honest guide / future adapter seam
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from openmw.openvault.paths import ensure_home
from openmw.openvault.ship.aws_guide import build_aws_render_plan
from openmw.openvault.ship.domain_guide import build_domain_guide
from openmw.openvault.ship.openship_client import adapter_status

# `cloudflare_pages` is the first target that genuinely publishes: it uploads
# the built artifact to the *user's own* Cloudflare account and attaches a
# domain they own. The others still produce plans or simulations — see
# ship/hosts/ for the distinction and docs/BACKEND_HONESTY_AUDIT.md for why it
# matters that the two are never conflated.
ShipTarget = Literal[
    "cloudflare_pages", "openship_cloud", "vps_ssh", "aws_guide", "local_demo"
]


@dataclass
class BillBudget:
    """Operator-controlled monthly spend ceiling (USD)."""

    monthly_cap_usd: float = 25.0
    spent_usd_estimate: float = 0.0
    soft_warn_pct: float = 80.0
    hard_stop: bool = True
    currency: str = "USD"
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        remaining = max(0.0, self.monthly_cap_usd - self.spent_usd_estimate)
        pct = (
            100.0 * self.spent_usd_estimate / self.monthly_cap_usd
            if self.monthly_cap_usd > 0
            else 0.0
        )
        return {
            **asdict(self),
            "remaining_usd": remaining,
            "used_pct": round(pct, 1),
            "blocked": self.hard_stop and self.spent_usd_estimate >= self.monthly_cap_usd,
            "warn": pct >= self.soft_warn_pct,
        }


def _budget_path() -> Path:
    path = ensure_home() / "billing"
    path.mkdir(parents=True, exist_ok=True)
    return path / "budget.json"


def load_bill_budget() -> BillBudget:
    path = _budget_path()
    if not path.is_file():
        return BillBudget()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return BillBudget()
    return BillBudget(
        monthly_cap_usd=float(raw.get("monthly_cap_usd", 25.0)),
        spent_usd_estimate=float(raw.get("spent_usd_estimate", 0.0)),
        soft_warn_pct=float(raw.get("soft_warn_pct", 80.0)),
        hard_stop=bool(raw.get("hard_stop", True)),
        currency=str(raw.get("currency", "USD")),
        updated_at=float(raw.get("updated_at", time.time())),
    )


def save_bill_budget(budget: BillBudget) -> BillBudget:
    budget.updated_at = time.time()
    _budget_path().write_text(json.dumps(budget.to_dict(), indent=2), encoding="utf-8")
    return budget


def record_spend(usd: float) -> BillBudget:
    budget = load_bill_budget()
    budget.spent_usd_estimate = max(0.0, budget.spent_usd_estimate + usd)
    return save_bill_budget(budget)


@dataclass
class TargetCard:
    id: ShipTarget
    title: str
    blurb: str
    instant_host: bool
    needs: list[str]
    register_url: str = ""
    estimated_min_usd: float = 0.0
    #: Paid placement — never merged into organic auto-select (CLAUDE_DECISIONS §8).
    sponsored: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TARGET_CARDS: tuple[TargetCard, ...] = (
    TargetCard(
        id="cloudflare_pages",
        title="Cloudflare Pages",
        blurb=(
            "Real publish — free tier, one token, custom domain API. "
            "Your machine builds; your Cloudflare account hosts. No OpenVault servers."
        ),
        instant_host=True,
        needs=["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "wrangler CLI"],
        register_url="https://dash.cloudflare.com/profile/api-tokens",
        estimated_min_usd=0.0,
    ),
    TargetCard(
        id="openship_cloud",
        title="FreeBuild Cloud",
        blurb="One-stop like Render — free *.opsh.io, managed TLS, credit quota, sleep mode.",
        instant_host=True,
        needs=["OPENSHIP_URL", "OPENSHIP_TOKEN", "Cloud connect in FreeBuild"],
        register_url="https://openship.io",
        estimated_min_usd=0.0,
    ),
    TargetCard(
        id="vps_ssh",
        title="Any VPS (Hetzner / DO / AWS EC2 / …)",
        blurb="SSH in → FreeBuild installs Docker + OpenResty + certbot. Same path for Hetzner.",
        instant_host=False,
        needs=["SSH host", "root/sudo user", "open ports 80/443"],
        register_url="https://www.hetzner.com/cloud",
        estimated_min_usd=4.0,
    ),
    TargetCard(
        id="aws_guide",
        title="AWS (S3 + ALB + Lambda-style + budgets)",
        blurb=(
            "Teach + register path until native adapter ships. "
            "Static near edge (CloudFront), LB, cache, Lambda, Budgets hard stop."
        ),
        instant_host=False,
        needs=["AWS account", "IAM user/role", "Billing alerts"],
        register_url="https://portal.aws.amazon.com/billing/signup",
        estimated_min_usd=5.0,
    ),
    TargetCard(
        id="local_demo",
        title="Local demo only",
        blurb="No public URL. Simulate ship + DNS teach. Laptop sleeps = site down.",
        instant_host=False,
        needs=[],
        estimated_min_usd=0.0,
    ),
)


def list_targets() -> dict[str, Any]:
    budget = load_bill_budget()
    adapter = adapter_status()
    organic = [t.to_dict() for t in TARGET_CARDS if not t.sponsored]
    sponsored = [t.to_dict() for t in TARGET_CARDS if t.sponsored]
    return {
        "targets": [t.to_dict() for t in TARGET_CARDS],
        "organic": organic,
        "sponsored": sponsored,
        "budget": budget.to_dict(),
        "openship": adapter,
        "product_promise": (
            "Your machine builds; your cloud account hosts; your domain points. "
            "OpenVault owns no servers. Sponsored targets never auto-select."
        ),
    }


def build_ship_blueprint(
    *,
    target: ShipTarget,
    project_path: str,
    hostname: str = "",
    github_url: str = "",
    vps_host: str = "",
    cloud_tier: str = "low",
    monthly_cap_usd: float | None = None,
) -> dict[str, Any]:
    """One-stop blueprint the wizard shows before Deploy."""
    if monthly_cap_usd is not None:
        budget = load_bill_budget()
        budget.monthly_cap_usd = float(monthly_cap_usd)
        save_bill_budget(budget)
    budget = load_bill_budget()
    card = next((t for t in TARGET_CARDS if t.id == target), TARGET_CARDS[-1])
    blocked = bool(budget.to_dict().get("blocked")) and target not in ("local_demo",)
    domain = build_domain_guide(hostname) if hostname else build_domain_guide("")
    aws = build_aws_render_plan(hostname=hostname or "app.example.com") if target == "aws_guide" else None

    steps: list[dict[str, str]] = []
    if target == "cloudflare_pages":
        steps = [
            "Add CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID to the vault (or env)",
            "Install wrangler once: npm install -g wrangler",
            "Preflight refuses early if token/wrangler missing",
            "Build on this machine → wrangler pages deploy (URL parsed from output)",
            f"Attach custom domain {hostname or 'your.domain'} (or paste CNAME if registrar is elsewhere)",
        ]
    elif target == "openship_cloud":
        steps = [
            "Connect FreeBuild Cloud (openship.io) — set OPENSHIP_URL + OPENSHIP_TOKEN",
            "Pick repo (GitHub OAuth in FreeBuild) or paste Git URL / local folder",
            "Pick Power tier (micro→high) — sleep mode saves credits",
            f"Free host *.opsh.io or custom {hostname or 'your.domain'}",
            "POST /deployments/build/access — FreeBuild builds containers (front + back)",
            "Watch SSE build stream → Visit Site",
        ]
    elif target == "vps_ssh":
        steps = [
            f"Rent VPS (Hetzner CX22 etc.) → note IP {vps_host or '<VPS_IP>'}",
            "FreeBuild: Add Server (SSH) → Test → Install Docker/OpenResty",
            "Point DNS A record to VPS IP (see domain guide)",
            "Deploy target=server with serverId — backend+frontend containers",
            "Certbot issues TLS on the box",
        ]
    elif target == "aws_guide":
        steps = [
            "Create AWS account + enable Billing alerts / Budgets hard stop",
            "Follow AWS resource plan (S3/CloudFront static, ALB, Lambda or ECS, ElastiCache)",
            "Register domain in Route53 or attach bought domain",
            "Set monthly_cap_usd in OpenVault — we block ship when estimate exceeds cap",
            "Native AWS adapter TBD — until then FreeBuild on EC2 SSH is the working path",
        ]
    else:
        steps = [
            "Local detect + simulate FreeBuild steps",
            "No public hostname — use for UI/dev only",
        ]

    return {
        "target": card.to_dict(),
        "project_path": project_path,
        "github_url": github_url,
        "hostname": hostname,
        "vps_host": vps_host,
        "cloud_tier": cloud_tier,
        "budget": budget.to_dict(),
        "blocked_by_budget": blocked,
        "domain_guide": domain.to_dict(),
        "aws_plan": aws.to_dict() if aws else None,
        "steps": steps,
        "openship": adapter_status(),
        "one_click_ready": (not blocked)
        and (
            target == "cloudflare_pages"
            or (target == "openship_cloud" and adapter_status()["api_ready"])
            or target == "local_demo"
            or (target == "vps_ssh" and bool(vps_host))
            or target == "aws_guide"
        ),
    }
