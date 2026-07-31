"""AWS Render/Vercel-shaped resource plan — teach + register until adapter ships.

FreeBuild does not call AWS APIs for ALB/S3/Lambda. This module produces the exact
checklist operators (or Claude) need: static near edge, LB, cache, Lambda-style
backend, DNS, and hard bill controls.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AwsResource:
    service: str
    role: str
    register_or_console: str
    notes: str
    estimated_usd_hint: str = ""


@dataclass
class AwsRenderPlan:
    """One-stop AWS blueprint mirroring Render: static + API + workers + DNS + budget."""

    hostname: str
    resources: list[AwsResource] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    bill_controls: list[str] = field(default_factory=list)
    hetzner_alternative: list[str] = field(default_factory=list)
    lambda_scaling_notes: list[str] = field(default_factory=list)
    openship_on_ec2: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hostname": self.hostname,
            "resources": [asdict(r) for r in self.resources],
            "steps": list(self.steps),
            "bill_controls": list(self.bill_controls),
            "hetzner_alternative": list(self.hetzner_alternative),
            "lambda_scaling_notes": list(self.lambda_scaling_notes),
            "openship_on_ec2": list(self.openship_on_ec2),
            "honest": (
                "Native AWS IaC adapter is not in FreeBuild. Prefer FreeBuild Cloud "
                "or FreeBuild-on-EC2/Hetzner SSH for working one-click today."
            ),
        }


def build_aws_render_plan(*, hostname: str) -> AwsRenderPlan:
    host = hostname.strip() or "app.example.com"
    resources = [
        AwsResource(
            "Route 53 / bought DNS",
            "DNS for apex + subdomain",
            "https://console.aws.amazon.com/route53/",
            f"A/AAAA or Alias to CloudFront/ALB for {host}",
            "$0.50/hosted zone",
        ),
        AwsResource(
            "S3",
            "Static frontend / assets",
            "https://console.aws.amazon.com/s3/",
            "Private bucket + OAC to CloudFront (never public ACL)",
            "pennies + egress",
        ),
        AwsResource(
            "CloudFront",
            "CDN — static near users",
            "https://console.aws.amazon.com/cloudfront/",
            "Cache static; origin = S3 and/or ALB API",
            "free tier then egress",
        ),
        AwsResource(
            "ACM",
            "TLS certs",
            "https://console.aws.amazon.com/acm/",
            "Request cert in us-east-1 for CloudFront",
            "free",
        ),
        AwsResource(
            "ALB",
            "Load balancer for HTTP API / containers",
            "https://console.aws.amazon.com/ec2/home#LoadBalancers:",
            "Target groups → ECS/EC2/Lambda",
            "~$16+/mo idle",
        ),
        AwsResource(
            "Lambda or ECS Fargate",
            "Backend / workers (serverless-ish scale)",
            "https://console.aws.amazon.com/lambda/",
            "API Gateway→Lambda OR ALB→Fargate for long requests",
            "pay per invoke / vCPU-hour",
        ),
        AwsResource(
            "ElastiCache Redis / MemoryDB",
            "Cache / sessions / queues",
            "https://console.aws.amazon.com/elasticache/",
            "Or run Redis container on FreeBuild VPS cheaper",
            "node-hour",
        ),
        AwsResource(
            "RDS Postgres (optional)",
            "Managed DB",
            "https://console.aws.amazon.com/rds/",
            "Or Docker Postgres via FreeBuild image catalog",
            "instance-hour",
        ),
        AwsResource(
            "AWS Budgets + Cost Anomaly",
            "Hard bill stop / alerts",
            "https://console.aws.amazon.com/billing/home#/budgets",
            "Budget alert at 50/80/100%; optional SNS → Lambda kill switch",
            "free",
        ),
    ]
    steps = [
        f"1. Register/login AWS → enable MFA → create IAM admin (not root day-to-day).",
        "2. Create Budget hard alert (match OpenVault monthly_cap_usd).",
        f"3. Request ACM cert for {host} (+ www).",
        "4. S3 bucket for static → CloudFront distribution (near edge).",
        "5. Backend: Lambda+API GW (short) OR ECS Fargate behind ALB (long/WebSocket).",
        "6. ElastiCache if you need Redis; else skip.",
        "7. DNS: alias {host} → CloudFront (static) / ALB (API subdomain).",
        "8. Until OpenVault AWS adapter ships: deploy FreeBuild on one EC2 and use SSH target.",
    ]
    bill_controls = [
        "AWS Budgets: absolute $ cap + forecasted > cap email",
        "Cost Anomaly Detection on the account",
        "OpenVault BillBudget.monthly_cap_usd blocks one-press when estimate exceeds",
        "Prefer FreeBuild Cloud credit quota (stop_workspaces) for hard stop without AWS bill surprise",
        "Tag every resource Project=Netie / Env=prod for Cost Explorer filters",
    ]
    hetzner = [
        "Often cheaper for always-on API: Hetzner CX22/CX32 + FreeBuild SSH install",
        "Register: https://www.hetzner.com/cloud → create server → copy IPv4",
        "FreeBuild Add Server → install Docker/OpenResty → Deploy target=server",
        "DNS A → Hetzner IP; FreeBuild certbot for TLS",
        "Scale later: bigger VPS or second node + external LB (not auto yet in FreeBuild)",
    ]
    lambda_notes = [
        "Lambda = event/API scale-to-zero; not a drop-in for long WebSockets",
        "Use Function URLs or API Gateway HTTP APIs for simple backends",
        "Cold starts: keep handlers warm or use Fargate/FreeBuild containers",
        "FreeBuild today runs containers/processes — closest Lambda analogue is Oblien sleep mode",
    ]
    openship_ec2 = [
        "Launch Ubuntu EC2 (t3.small+), open 22/80/443, attach Elastic IP",
        "Set OPENSHIP_URL to your control plane OR run openship on the box",
        "Add server SSH → install → deploy — same as Hetzner path",
        "Optional: put CloudFront in front of the EC2/OpenResty for static near",
    ]
    return AwsRenderPlan(
        hostname=host,
        resources=resources,
        steps=steps,
        bill_controls=bill_controls,
        hetzner_alternative=hetzner,
        lambda_scaling_notes=lambda_notes,
        openship_on_ec2=openship_ec2,
    )
