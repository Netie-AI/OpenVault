"""Stripe Checkout for OpenVault SKUs. Hosted subscription, test-mode prices.

Uses httpx against the Stripe REST API when STRIPE_SECRET_KEY is set and
STRIPE_MODE=live. Default is simulate so CI and laptops never charge a card.

Does not import AirGPT, DMS, or the trust root. Price IDs are the NETIE
test-mode products (acct_1RMx9FFV5wcFod2f).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import structlog

from openmw.openvault.ship.service import (
    SKUS,
    SkuId,
    attach_checkout_id,
    load_session,
    mark_session_billed,
)

log = structlog.get_logger()

# NETIE Stripe test-mode default_price on products ov_hosted / ov_fast / byo_*
_TEST_PRICES: dict[SkuId, str] = {
    "ov_hosted": "price_1U8SQSFV5wcFod2fggATWBtT",
    "ov_fast": "price_1U8SQbFV5wcFod2fBfkEFyl4",
    "byo_aws": "price_1U8SQbFV5wcFod2fv5r1WD8o",
    "byo_vps": "price_1U8SQcFV5wcFod2fw5s1cqSs",
}


def stripe_mode() -> str:
    mode = os.environ.get("STRIPE_MODE", "simulate").strip().lower()
    if mode == "live" and os.environ.get("STRIPE_SECRET_KEY"):
        return "live"
    return "simulate"


def price_id_for(sku_id: SkuId) -> str:
    env_key = f"STRIPE_PRICE_{sku_id.upper()}"
    override = os.environ.get(env_key, "").strip()
    if override:
        return override
    sku = SKUS[sku_id]
    return sku.stripe_price_id or _TEST_PRICES[sku_id]


def create_checkout(
    session_id: str,
    *,
    success_url: str = "http://127.0.0.1:5000/#service",
    cancel_url: str = "http://127.0.0.1:5000/#service",
) -> dict[str, Any]:
    """Create a Stripe Checkout Session in subscription mode for the SKU."""
    session = load_session(session_id)
    if session is None:
        raise ValueError("service session not found")
    price = price_id_for(session.sku_id)
    sku = SKUS[session.sku_id]
    if stripe_mode() == "live":
        payload = _live_checkout(
            email=session.email,
            price_id=price,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"ov_session": session_id, "sku": session.sku_id},
        )
        attach_checkout_id(session_id, str(payload.get("id") or ""))
        return payload
    checkout_id = f"cs_test_sim_{uuid.uuid4().hex[:16]}"
    attach_checkout_id(session_id, checkout_id)
    log.info("stripe_checkout_simulate", session_id=session_id, sku=session.sku_id)
    return {
        "id": checkout_id,
        "object": "checkout.session",
        "mode": "subscription",
        "url": f"https://checkout.stripe.com/c/pay/{checkout_id}",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "customer_email": session.email,
        "amount_total": int(sku.monthly_usd * 100),
        "currency": "usd",
        "metadata": {"ov_session": session_id, "sku": session.sku_id},
        "simulated": True,
        "livemode": False,
        "payment_status": "unpaid",
        "price_id": price,
    }


def confirm_checkout(session_id: str, *, checkout_id: str = "") -> dict[str, Any]:
    """Mark the OpenVault session billed after Checkout succeeds (or simulate)."""
    session = load_session(session_id)
    if session is None:
        raise ValueError("service session not found")
    cid = checkout_id or session.stripe_checkout_id
    if stripe_mode() == "live" and cid:
        paid, sub = _live_checkout_status(cid)
        if not paid:
            raise ValueError("stripe checkout is not paid")
        updated = mark_session_billed(session_id, checkout_id=cid, subscription_id=sub)
        return {"ok": True, "simulated": False, "session": updated.to_dict()}
    sub = f"sub_sim_{uuid.uuid4().hex[:12]}"
    updated = mark_session_billed(session_id, checkout_id=cid, subscription_id=sub)
    return {"ok": True, "simulated": True, "session": updated.to_dict()}


def apply_checkout_event(event: dict[str, Any]) -> dict[str, Any]:
    """Handle checkout.session.completed. Ignores other event types."""
    kind = str(event.get("type") or "")
    if kind != "checkout.session.completed":
        return {"ok": True, "ignored": kind or "unknown"}
    data = event.get("data")
    obj: dict[str, Any] = {}
    if isinstance(data, dict):
        inner = data.get("object")
        if isinstance(inner, dict):
            obj = inner
    meta = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    session_id = str(meta.get("ov_session") or "")
    if not session_id:
        raise ValueError("checkout event missing ov_session metadata")
    checkout_id = str(obj.get("id") or "")
    sub = str(obj.get("subscription") or "")
    updated = mark_session_billed(session_id, checkout_id=checkout_id, subscription_id=sub)
    return {"ok": True, "session": updated.to_dict()}


def _live_checkout(
    *,
    email: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str],
) -> dict[str, Any]:
    secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not secret:
        raise ValueError("STRIPE_SECRET_KEY required for STRIPE_MODE=live")
    form: dict[str, str] = {
        "mode": "subscription",
        "success_url": f"{success_url}?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": cancel_url,
        "customer_email": email,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "client_reference_id": metadata.get("ov_session", ""),
    }
    for key, value in metadata.items():
        form[f"metadata[{key}]"] = value
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            "https://api.stripe.com/v1/checkout/sessions",
            data=form,
            auth=(secret, ""),
        )
    if response.status_code >= 400:
        raise ValueError(f"stripe checkout failed: {response.text[:500]}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("stripe checkout returned non-object")
    return payload


def _live_checkout_status(checkout_id: str) -> tuple[bool, str]:
    secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not secret:
        return False, ""
    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            f"https://api.stripe.com/v1/checkout/sessions/{checkout_id}",
            auth=(secret, ""),
        )
    if response.status_code >= 400:
        return False, ""
    payload = response.json()
    if not isinstance(payload, dict):
        return False, ""
    paid = str(payload.get("payment_status") or "") == "paid"
    sub = str(payload.get("subscription") or "")
    return paid, sub
