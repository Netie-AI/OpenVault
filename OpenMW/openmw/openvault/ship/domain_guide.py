"""DNS / load-balancer record guide for a public hostname."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DomainGuide:
    hostname: str
    records: list[dict[str, str]] = field(default_factory=list)
    tls: str = "Issue at the load balancer / Vercel / Caddy (HTTP-01 or DNS-01)."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_domain_guide(
    hostname: str,
    *,
    target_a: str = "<YOUR_SERVER_IP>",
    target_cname: str = "",
    include_www: bool = True,
    include_mail: bool = True,
) -> DomainGuide:
    host = hostname.strip().rstrip(".")
    records: list[dict[str, str]] = []
    if target_cname:
        records.append({"type": "CNAME", "name": host, "value": target_cname})
    else:
        records.append({"type": "A", "name": host, "value": target_a})
    if include_www:
        records.append({"type": "CNAME", "name": f"www.{host}", "value": host})
    if include_mail:
        records.append({"type": "MX", "name": host, "value": f"mail.{host}"})
        records.append({"type": "TXT", "name": host, "value": "v=spf1 mx -all"})
    return DomainGuide(hostname=host, records=records)
