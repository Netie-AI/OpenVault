"""Precise domain / DNS guide for a bought hostname (FreeBuild subdomain path).

Does not call registrar APIs — produces an actionable checklist the operator
(or Claude) can follow at Namecheap / Cloudflare / Route53 / etc.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DnsRecordHint:
    """One DNS record the operator should create/verify."""

    type: str
    name: str
    value: str
    ttl_hint: str = "300"
    purpose: str = ""


@dataclass
class DomainGuide:
    """Step-by-step guide for wiring a purchased domain into FreeBuild."""

    hostname: str
    apex: str
    subdomain_label: str
    records: list[DnsRecordHint] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    email_auth_notes: list[str] = field(default_factory=list)
    registrar_hints: list[str] = field(default_factory=list)
    ready_when: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hostname": self.hostname,
            "apex": self.apex,
            "subdomain_label": self.subdomain_label,
            "records": [asdict(r) for r in self.records],
            "steps": list(self.steps),
            "email_auth_notes": list(self.email_auth_notes),
            "registrar_hints": list(self.registrar_hints),
            "ready_when": list(self.ready_when),
        }


def _split_host(hostname: str) -> tuple[str, str, str]:
    host = hostname.strip().lower().rstrip(".")
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return host, host, ""
    if len(parts) == 2:
        return host, host, "@"
    apex = ".".join(parts[-2:])
    label = ".".join(parts[:-2])
    return host, apex, label


def build_domain_guide(
    hostname: str,
    *,
    target_a: str = "<YOUR_SERVER_IP>",
    target_cname: str = "",
    include_www: bool = True,
    include_mail: bool = True,
) -> DomainGuide:
    """Build a precise DNS + TLS checklist for ``hostname`` (e.g. app.example.com)."""
    host, apex, label = _split_host(hostname)
    records: list[DnsRecordHint] = []
    steps: list[str] = []

    if not host or "." not in host:
        return DomainGuide(
            hostname=host or hostname,
            apex=apex,
            subdomain_label=label,
            steps=[
                "Enter a full hostname like app.example.com (bought domain + subdomain).",
                "OpenVault will then emit A/CNAME, TLS, and mail DNS steps.",
            ],
            registrar_hints=[
                "Buy/transfer the apex at your registrar; point nameservers if using Cloudflare.",
            ],
            ready_when=["hostname contains at least one dot"],
        )

    if label in ("", "@"):
        records.append(
            DnsRecordHint(
                type="A",
                name="@",
                value=target_a,
                purpose="Apex points at the FreeBuild host",
            )
        )
        if include_www:
            records.append(
                DnsRecordHint(
                    type="CNAME",
                    name="www",
                    value=apex,
                    purpose="www → apex",
                )
            )
    else:
        cname_target = target_cname or apex
        if target_a and target_a != "<YOUR_SERVER_IP>" and not target_cname:
            records.append(
                DnsRecordHint(
                    type="A",
                    name=label,
                    value=target_a,
                    purpose=f"{host} → server IP",
                )
            )
        else:
            records.append(
                DnsRecordHint(
                    type="CNAME",
                    name=label,
                    value=cname_target,
                    purpose=f"{host} → {cname_target} (or set A to server IP)",
                )
            )

    steps.extend(
        [
            f"1. At your registrar/DNS for apex `{apex}`, create the records listed below.",
            f"2. Wait for DNS (often 1-30 min). Verify: `nslookup {host}` or `dig {host}`.",
            (
                f"3. FreeBuild TLS step issues Let's Encrypt for `{host}` "
                "once HTTP-01/DNS-01 can reach the host."
            ),
            "4. Set OPENSHIP_MODE=cli (or api) with a real adapter when you leave simulate mode.",
            f"5. Smoke: open https://{host} after execute; use Playwright smoke in the Deploy tab.",
        ]
    )

    email_notes: list[str] = []
    if include_mail:
        email_notes = [
            (
                f"SPF: TXT @ -> `v=spf1 ip4:{target_a} -all` "
                "(replace IP; include ESP includes if using SES/Postmark)."
            ),
            "DKIM: add the TXT/CNAME your ESP gives (selector._domainkey).",
            (
                "DMARC: TXT `_dmarc` -> `v=DMARC1; p=none; rua=mailto:dmarc@"
                + apex
                + "` then tighten to quarantine/reject."
            ),
            "PTR: reverse DNS on the sending IP must match a hostname under " + apex + ".",
        ]
        steps.append(
            "6. If the app sends mail, complete SPF/DKIM/DMARC/PTR before marking email_auth pass."
        )

    registrar_hints = [
        "Cloudflare: add site -> update nameservers at registrar -> DNS tab -> "
        "Proxy off (DNS only) until TLS works.",
        "Namecheap: Advanced DNS → Add Record; disable parking page / URL redirect for this host.",
        "Route53: Hosted zone for apex -> Create record; attach ACM cert only "
        "if terminating at AWS ALB.",
        "Vercel/Netlify: if using their DNS, CNAME the subdomain to their "
        "target instead of a raw IP.",
    ]

    ready_when = [
        f"`nslookup {host}` returns the expected A/CNAME",
        f"https://{host} serves the app (or FreeBuild simulate reported ready)",
        "TLS cert issued (no browser warning)",
        "email_auth gate pass or skipped when mail not required",
    ]

    return DomainGuide(
        hostname=host,
        apex=apex,
        subdomain_label=label or "@",
        records=records,
        steps=steps,
        email_auth_notes=email_notes,
        registrar_hints=registrar_hints,
        ready_when=ready_when,
    )
