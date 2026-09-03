"""End-to-end OpenVault ship onto a netie.ai hostname.

Login -> Stripe SKU checkout -> Caddy/systemd auto-host. Isolated from AirGPT,
DMS trust-root, and Cortex mesh. Default domain suffix is netie.ai.
"""

from __future__ import annotations

from typing import Any

from openmw.openvault.ship.detect import detect_project
from openmw.openvault.ship.origin import build_origin_plan, execute_origin_plan
from openmw.openvault.ship.server import build_server_plan, execute_server_plan
from openmw.openvault.ship.service import (
    SkuId,
    auto_host,
    login_service,
    parse_sku_id,
)
from openmw.openvault.ship.stripe_billing import confirm_checkout, create_checkout

NETIE_HTTP_SUFFIX = "netie.ai"


def netie_hostname(display_name: str, *, suffix: str = NETIE_HTTP_SUFFIX) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in display_name.lower()).strip("-")
    return f"{slug or 'app'}.{suffix}"


def ship_to_netie(
    *,
    email: str,
    project_path: str,
    display_name: str = "",
    sku_id: SkuId | str | None = "ov_hosted",
    simulate: bool = True,
) -> dict[str, Any]:
    """Charge the SKU (simulate unless STRIPE_MODE=live) and ship HTTP to *.netie.ai."""
    stack = detect_project(project_path)
    parsed = parse_sku_id(sku_id if isinstance(sku_id, str) else None)
    chosen: SkuId = parsed or "ov_hosted"
    session = login_service(
        email=email,
        display_name=display_name or email.split("@")[0],
        login_kind="openvault",
        sku_id=chosen,
    )
    host = netie_hostname(session.display_name)
    checkout = create_checkout(session.session_id)
    paid = confirm_checkout(session.session_id, checkout_id=str(checkout.get("id") or ""))
    hosted = auto_host(
        session.session_id,
        project_path=project_path,
        hostname=host,
        simulate=simulate,
    )
    origin = build_origin_plan(project_path=project_path, hostname=host, stack=stack)
    origin_executed = execute_origin_plan(origin, simulate=simulate)
    server_plan = build_server_plan(
        project_path=project_path,
        hostname=host,
        vps_host=str((hosted.get("session") or {}).get("vps_host") or ""),
        target="openvault_hosted",
        stack=stack,
    )
    server = execute_server_plan(server_plan, simulate=simulate)
    return {
        "ok": bool(paid.get("ok")) and server.executed,
        "laptop": False,
        "domain": NETIE_HTTP_SUFFIX,
        "hostname": host,
        "airgpt": False,
        "dms": False,
        "session": paid.get("session") or hosted.get("session"),
        "checkout": checkout,
        "auto_host": hosted,
        "origin": origin_executed.to_dict(),
        "server": server.to_dict(),
        "stack": stack.to_dict(),
    }
