"""OpenVault Service: login, packaged prices, auto-host. Not the laptop.

Customers log into OpenVault (or connect AWS MCP / their VPS / their own
server). We wrap AWS Lightsail and a generic VPS as OpenVault SKUs with our
prices. Caddy is the load balancer. Laptop/local_demo is operator-only.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

from openmw.openvault.paths import ensure_home
from openmw.openvault.ship.detect import detect_project
from openmw.openvault.ship.hosting import host_kind_for
from openmw.openvault.ship.server import build_server_plan, execute_server_plan

log = structlog.get_logger()

LoginKind = Literal["openvault", "aws", "vps", "own_server"]
SkuId = Literal["ov_hosted", "ov_fast", "byo_aws", "byo_vps"]

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class ServiceSku:
    id: SkuId
    name: str
    monthly_usd: float
    wraps: tuple[str, ...]
    load_balancer: str
    auto_host: bool
    laptop: bool
    customer_pays_infra: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["wraps"] = list(self.wraps)
        return payload


SKUS: dict[SkuId, ServiceSku] = {
    "ov_hosted": ServiceSku(
        id="ov_hosted",
        name="OpenVault Hosted",
        monthly_usd=24.0,
        wraps=("aws_lightsail", "vps"),
        load_balancer="caddy",
        auto_host=True,
        laptop=False,
        customer_pays_infra=False,
        detail=(
            "Suggested. We wrap AWS Lightsail and a spare VPS as one OpenVault "
            "product: Caddy TLS, systemd, /healthz. You log into OpenVault, not AWS."
        ),
    ),
    "ov_fast": ServiceSku(
        id="ov_fast",
        name="OpenVault Fast",
        monthly_usd=79.0,
        wraps=("dedicated_vps", "own_server"),
        load_balancer="caddy",
        auto_host=True,
        laptop=False,
        customer_pays_infra=False,
        detail=(
            "Dedicated box (or their own server we operate). Faster IO. "
            "Same Caddy + systemd, higher OpenVault price."
        ),
    ),
    "byo_aws": ServiceSku(
        id="byo_aws",
        name="Your AWS",
        monthly_usd=9.0,
        wraps=("aws_account",),
        load_balancer="caddy",
        auto_host=False,
        laptop=False,
        customer_pays_infra=True,
        detail=(
            "Log into AWS (MCP / SSM / instance profile). "
            "They pay AWS; we charge the platform fee."
        ),
    ),
    "byo_vps": ServiceSku(
        id="byo_vps",
        name="Your VPS",
        monthly_usd=9.0,
        wraps=("customer_vps",),
        load_balancer="caddy",
        auto_host=False,
        laptop=False,
        customer_pays_infra=True,
        detail="Log into their VPS over SSH. They pay the VPS; we charge the platform fee.",
    ),
}


@dataclass
class ServiceSession:
    session_id: str
    email: str
    display_name: str
    login_kind: LoginKind
    sku_id: SkuId
    account_id: str = ""
    hostname: str = ""
    vps_host: str = ""
    aws_region: str = ""
    aws_account_hint: str = ""
    aws_mcp: bool = False
    connected: bool = False
    laptop: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        sku = SKUS[self.sku_id]
        return {
            "session_id": self.session_id,
            "email": self.email,
            "display_name": self.display_name,
            "login_kind": self.login_kind,
            "sku": sku.to_dict(),
            "account_id": self.account_id,
            "hostname": self.hostname,
            "vps_host": self.vps_host,
            "aws_region": self.aws_region,
            "aws_account_hint": self.aws_account_hint,
            "aws_mcp": self.aws_mcp,
            "connected": self.connected,
            "laptop": False,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def service_catalog() -> dict[str, Any]:
    return {
        "product": "OpenVault Service",
        "laptop": False,
        "load_balancer": "caddy",
        "suggested_sku": "ov_hosted",
        "currency": "USD",
        "skus": [sku.to_dict() for sku in SKUS.values()],
        "login_kinds": [
            {
                "id": "openvault",
                "name": "OpenVault Service",
                "detail": "No self-host. We wrap AWS + VPS. Auto-host on login.",
            },
            {
                "id": "aws",
                "name": "AWS (MCP / SSM)",
                "detail": "Log into their AWS. OpenVault still owns Caddy + systemd.",
            },
            {
                "id": "vps",
                "name": "Their VPS",
                "detail": "SSH to the box they already pay for.",
            },
            {
                "id": "own_server",
                "name": "Their own server (Fast)",
                "detail": "Dedicated metal they bring; OpenVault Fast price.",
            },
        ],
        "notes": [
            "Do not run the customer app on their laptop.",
            "Secrets are not stored; AWS uses MCP/SSM/instance profile.",
        ],
    }


def parse_login_kind(raw: str) -> LoginKind:
    if raw == "aws":
        return "aws"
    if raw == "vps":
        return "vps"
    if raw == "own_server":
        return "own_server"
    if raw == "openvault":
        return "openvault"
    raise ValueError("login_kind must be openvault, aws, vps, or own_server")


def parse_sku_id(raw: str | None) -> SkuId | None:
    if raw is None or raw == "":
        return None
    if raw == "ov_fast":
        return "ov_fast"
    if raw == "byo_aws":
        return "byo_aws"
    if raw == "byo_vps":
        return "byo_vps"
    if raw == "ov_hosted":
        return "ov_hosted"
    raise ValueError("sku_id must be ov_hosted, ov_fast, byo_aws, or byo_vps")


def suggest_sku(login_kind: LoginKind, *, host_kind: str = "") -> SkuId:
    if login_kind == "aws":
        return "byo_aws"
    if login_kind == "vps":
        return "byo_vps"
    if login_kind == "own_server":
        return "ov_fast"
    del host_kind
    return "ov_hosted"


def quote(
    *,
    login_kind: LoginKind = "openvault",
    project_path: str = "",
    sku_id: SkuId | None = None,
) -> dict[str, Any]:
    host_kind = ""
    framework = ""
    if project_path:
        stack = detect_project(project_path)
        host_kind = host_kind_for(stack)
        framework = stack.framework or stack.primary
    chosen = sku_id or suggest_sku(login_kind, host_kind=host_kind)
    sku = SKUS[chosen]
    line = {
        "name": sku.name,
        "usd": sku.monthly_usd,
        "wraps": list(sku.wraps),
    }
    extra = (
        "Customer also pays their AWS/VPS invoice."
        if sku.customer_pays_infra
        else "Infra is inside the OpenVault price (we wrap AWS Lightsail or a VPS)."
    )
    return {
        "login_kind": login_kind,
        "framework": framework,
        "host_kind": host_kind,
        "sku": sku.to_dict(),
        "monthly_usd": sku.monthly_usd,
        "currency": "USD",
        "line_items": [line],
        "infra_note": extra,
        "laptop": False,
        "load_balancer": "caddy",
        "suggested": chosen == suggest_sku(login_kind, host_kind=host_kind),
    }


def _sessions_dir() -> Path:
    path = ensure_home() / "service_sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(value: str) -> str:
    cleaned = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return cleaned or "app"


def login_service(
    *,
    email: str,
    display_name: str = "",
    login_kind: LoginKind = "openvault",
    account_id: str = "",
    sku_id: SkuId | None = None,
) -> ServiceSession:
    if login_kind not in {"openvault", "aws", "vps", "own_server"}:
        raise ValueError(f"unsupported login_kind: {login_kind}")
    addr = email.strip().lower()
    if "@" not in addr or "." not in addr.split("@")[-1]:
        raise ValueError("email required to log into OpenVault Service")
    chosen = sku_id or suggest_sku(login_kind)
    now = time.time()
    session = ServiceSession(
        session_id=uuid.uuid4().hex[:16],
        email=addr,
        display_name=display_name.strip() or addr.split("@")[0],
        login_kind=login_kind,
        sku_id=chosen,
        account_id=account_id,
        connected=login_kind == "openvault",
        aws_mcp=login_kind == "aws",
        laptop=False,
        created_at=now,
        updated_at=now,
    )
    if login_kind == "openvault":
        session.hostname = f"{_slug(session.display_name)}.openvault.app"
        session.vps_host = f"ov-hosted-{session.session_id[:8]}"
    _save_session(session)
    log.info(
        "service_login",
        session_id=session.session_id,
        login_kind=login_kind,
        sku=chosen,
    )
    return session


def connect_service(
    session_id: str,
    *,
    login_kind: LoginKind,
    vps_host: str = "",
    hostname: str = "",
    aws_region: str = "",
    aws_account_hint: str = "",
    secret: str = "",
) -> ServiceSession:
    """Attach AWS MCP, a VPS, or their own server. `secret` is never persisted."""
    del secret  # refused: instance profile / MCP / SSH keys on the box instead
    session = load_session(session_id)
    if session is None:
        raise ValueError("service session not found")
    if login_kind == "openvault":
        raise ValueError("openvault login does not connect a customer box")
    session.login_kind = login_kind
    session.sku_id = suggest_sku(login_kind)
    session.connected = True
    session.laptop = False
    session.updated_at = time.time()
    if hostname.strip():
        session.hostname = hostname.strip()
    if login_kind == "aws":
        session.aws_mcp = True
        session.aws_region = aws_region.strip() or "us-east-1"
        session.aws_account_hint = aws_account_hint.strip()[:12]
        session.vps_host = vps_host.strip() or session.vps_host or "<aws-instance>"
    else:
        if not vps_host.strip():
            raise ValueError("vps_host required to log into a VPS or own server")
        session.vps_host = vps_host.strip()
        session.aws_mcp = False
    _save_session(session)
    log.info("service_connect", session_id=session.session_id, login_kind=login_kind)
    return session


def auto_host(
    session_id: str,
    *,
    project_path: str = "",
    hostname: str = "",
    simulate: bool = True,
) -> dict[str, Any]:
    """Assign OpenVault-wrapped AWS/VPS and emit the Caddy/systemd plan."""
    session = load_session(session_id)
    if session is None:
        raise ValueError("service session not found")
    sku = SKUS[session.sku_id]
    if not sku.auto_host and not session.vps_host:
        raise ValueError("connect AWS or a VPS before auto-host, or pick OpenVault Hosted")
    if hostname.strip():
        session.hostname = hostname.strip()
    if not session.hostname:
        session.hostname = f"{_slug(session.display_name)}.openvault.app"
    if not session.vps_host:
        session.vps_host = f"ov-hosted-{session.session_id[:8]}"
    session.connected = True
    session.updated_at = time.time()
    _save_session(session)

    target = _ship_target(session)
    server: dict[str, Any] = {}
    if project_path:
        plan = build_server_plan(
            project_path=project_path,
            hostname=session.hostname,
            vps_host=session.vps_host,
            target=target,
        )
        executed = execute_server_plan(plan, simulate=simulate)
        server = executed.to_dict()
    return {
        "session": session.to_dict(),
        "quote": quote(
            login_kind=session.login_kind, project_path=project_path, sku_id=session.sku_id
        ),
        "server": server,
        "laptop": False,
        "ship_target": target,
    }


def load_session(session_id: str) -> ServiceSession | None:
    path = _sessions_dir() / f"{session_id}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    sku_raw = str(raw.get("sku_id") or "ov_hosted")
    sku_id = parse_sku_id(sku_raw) or "ov_hosted"
    login_kind = parse_login_kind(str(raw.get("login_kind") or "openvault"))
    return ServiceSession(
        session_id=raw["session_id"],
        email=str(raw.get("email") or ""),
        display_name=str(raw.get("display_name") or ""),
        login_kind=login_kind,
        sku_id=sku_id,
        account_id=str(raw.get("account_id") or ""),
        hostname=str(raw.get("hostname") or ""),
        vps_host=str(raw.get("vps_host") or ""),
        aws_region=str(raw.get("aws_region") or ""),
        aws_account_hint=str(raw.get("aws_account_hint") or ""),
        aws_mcp=bool(raw.get("aws_mcp")),
        connected=bool(raw.get("connected")),
        laptop=False,
        created_at=float(raw.get("created_at", time.time())),
        updated_at=float(raw.get("updated_at", time.time())),
    )


def _save_session(session: ServiceSession) -> Path:
    path = _sessions_dir() / f"{session.session_id}.json"
    raw = {
        "session_id": session.session_id,
        "email": session.email,
        "display_name": session.display_name,
        "login_kind": session.login_kind,
        "sku_id": session.sku_id,
        "account_id": session.account_id,
        "hostname": session.hostname,
        "vps_host": session.vps_host,
        "aws_region": session.aws_region,
        "aws_account_hint": session.aws_account_hint,
        "aws_mcp": session.aws_mcp,
        "connected": session.connected,
        "laptop": False,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return path


def _ship_target(session: ServiceSession) -> str:
    if session.login_kind == "aws":
        return "aws"
    if session.login_kind == "own_server":
        return "vps_ssh"
    if session.sku_id == "ov_hosted":
        return "openvault_hosted"
    return "vps_ssh"
