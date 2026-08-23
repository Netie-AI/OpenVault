"""Secure email DNS gate checklist (SPF / DKIM / DMARC / PTR)."""

from __future__ import annotations

import socket
from dataclasses import asdict, dataclass
from typing import Any, Literal

GateStatus = Literal["pass", "fail", "pending", "skipped"]


@dataclass(frozen=True)
class EmailGateResult:
    name: str
    status: GateStatus
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _txt_lookup(name: str) -> list[str]:
    try:
        import dns.resolver  # type: ignore[import-not-found]
    except ImportError:
        return []
    try:
        answers = dns.resolver.resolve(name, "TXT")
        out: list[str] = []
        for rdata in answers:
            parts = []
            for b in rdata.strings:
                parts.append(b.decode("utf-8") if isinstance(b, bytes) else str(b))
            out.append("".join(parts))
        return out
    except Exception:
        return []


def check_email_auth(domain: str, *, sending_ip: str | None = None) -> list[EmailGateResult]:
    """Check deliverability primitives that actually matter for inbox placement."""
    if not domain or "." not in domain:
        return [
            EmailGateResult("spf", "fail", "invalid or missing domain"),
            EmailGateResult("dkim", "fail", "invalid or missing domain"),
            EmailGateResult("dmarc", "fail", "invalid or missing domain"),
            EmailGateResult("ptr", "skipped", "no sending_ip provided"),
        ]

    results: list[EmailGateResult] = []

    spf_records = _txt_lookup(domain)
    if not spf_records:
        results.append(
            EmailGateResult(
                "spf",
                "pending",
                f"TXT lookup unavailable or empty for {domain} — publish v=spf1 …",
            )
        )
    elif any("v=spf1" in r.lower() for r in spf_records):
        results.append(EmailGateResult("spf", "pass", "SPF TXT found"))
    else:
        results.append(EmailGateResult("spf", "fail", "TXT present but no v=spf1"))

    # DKIM selectors vary; check common selectors
    dkim_ok = False
    for selector in ("default", "google", "selector1", "s1", "mail"):
        recs = _txt_lookup(f"{selector}._domainkey.{domain}")
        if any("v=DKIM1" in r or "k=rsa" in r.lower() for r in recs):
            dkim_ok = True
            results.append(EmailGateResult("dkim", "pass", f"DKIM at selector={selector}"))
            break
    if not dkim_ok:
        results.append(
            EmailGateResult(
                "dkim",
                "pending",
                "No common DKIM selector found — configure FreeBuild/mail DKIM",
            )
        )

    dmarc = _txt_lookup(f"_dmarc.{domain}")
    if any("v=DMARC1" in r for r in dmarc):
        results.append(EmailGateResult("dmarc", "pass", "DMARC TXT found"))
    elif dmarc:
        results.append(EmailGateResult("dmarc", "fail", "TXT at _dmarc but not DMARC"))
    else:
        results.append(
            EmailGateResult(
                "dmarc",
                "pending",
                "Publish _dmarc TXT (p=none → quarantine → reject)",
            )
        )

    if sending_ip:
        try:
            host, _, _ = socket.gethostbyaddr(sending_ip)
            results.append(EmailGateResult("ptr", "pass", f"PTR → {host}"))
        except (socket.herror, socket.gaierror, OSError):
            results.append(
                EmailGateResult("ptr", "fail", f"No PTR for {sending_ip} — fix reverse DNS")
            )
    else:
        results.append(
            EmailGateResult("ptr", "skipped", "Provide sending_ip to verify reverse DNS")
        )

    results.append(
        EmailGateResult(
            "reputation_notes",
            "pending",
            ("Track: IP warming, bounces, complaints, blocklists, throttling, inbox placement"),
        )
    )
    return results
