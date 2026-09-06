"""Prepaid experience packs for mixed-hop credit (DR-0013).

Not hosting SKUs. Not a live Stripe charge. 80% of the sticker is estimated
API credit on pooled hops. Exhaustion is a typed 402 with a free/BYOK path.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from openmw.openvault.paths import ensure_home

PackId = Literal["starter", "plus", "pro", "studio"]

#: Fraction of sticker price that becomes hop credit ($10 -> $8).
API_SHARE = 0.8
#: Estimated blended USD per 1M billable tokens. Not a provider price table.
DEFAULT_BLEND_USD_PER_1M = 0.20

STUCK_NEXT_STEPS: tuple[str, ...] = (
    "Stay on free hops while they last",
    "Free keys: Register, then Install",
    "Bring your own key",
    "Buy a larger pack",
)


@dataclass(frozen=True)
class ExperiencePack:
    id: PackId
    price_usd: float
    api_credit_usd: float
    title: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PACKS: dict[PackId, ExperiencePack] = {
    "starter": ExperiencePack(
        id="starter",
        price_usd=10.0,
        api_credit_usd=8.0,
        title="Start",
        detail="First ecosystem access. About $8 mixed-hop credit. Simulate checkout.",
    ),
    "plus": ExperiencePack(
        id="plus",
        price_usd=30.0,
        api_credit_usd=24.0,
        title="Plus",
        detail="More mixed-hop credit on the same pooled path.",
    ),
    "pro": ExperiencePack(
        id="pro",
        price_usd=100.0,
        api_credit_usd=80.0,
        title="Pro",
        detail="Team-sized mixed-hop credit.",
    ),
    "studio": ExperiencePack(
        id="studio",
        price_usd=500.0,
        api_credit_usd=400.0,
        title="Studio",
        detail="Studio-sized mixed-hop credit.",
    ),
}


@dataclass
class PackBalance:
    api_key_id: str
    pack_id: PackId
    credit_usd: float
    mode: str = "simulate"
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def blend_usd_per_1m() -> float:
    raw = (os.environ.get("OPENVAULT_BLEND_USD_PER_1M") or "").strip()
    if not raw:
        return DEFAULT_BLEND_USD_PER_1M
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_BLEND_USD_PER_1M
    return value if value > 0 else DEFAULT_BLEND_USD_PER_1M


def estimated_usd(billable_tokens: int, *, usd_per_1m: float | None = None) -> float:
    rate = blend_usd_per_1m() if usd_per_1m is None else usd_per_1m
    return round(max(0, int(billable_tokens)) * rate / 1_000_000.0, 6)


def parse_pack_id(raw: str | None) -> PackId | None:
    name = (raw or "").strip().lower()
    if name in PACKS:
        return name  # type: ignore[return-value]
    return None


def _store_path() -> Path:
    return ensure_home() / "route_packs.json"


def _load() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {"balances": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"balances": {}}
    balances = raw.get("balances") if isinstance(raw, dict) else None
    if not isinstance(balances, dict):
        return {"balances": {}}
    return {"balances": balances}


def _save(state: dict[str, Any]) -> None:
    _store_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def list_packs() -> dict[str, Any]:
    return {
        "packs": [p.to_dict() for p in PACKS.values()],
        "api_share": API_SHARE,
        "blend_usd_per_1m": blend_usd_per_1m(),
        "blend_estimated": True,
        # Packs have no live price ids. Hosting SKUs may be STRIPE_MODE=live;
        # this catalog must not look chargeable until those ids exist.
        "stripe_mode": "simulate",
        "stuck_next_steps": list(STUCK_NEXT_STEPS),
        "note": (
            "Hosting SKUs are a different product. These packs are mixed-hop "
            "credit on pooled keys. Checkout is simulate until live price ids exist."
        ),
    }


def get_balance(api_key_id: str) -> PackBalance | None:
    key = (api_key_id or "").strip()
    if not key:
        return None
    raw = _load()["balances"].get(key)
    if not isinstance(raw, dict):
        return None
    pack_id = parse_pack_id(str(raw.get("pack_id") or ""))
    if pack_id is None:
        return None
    try:
        credit = float(raw.get("credit_usd") or 0.0)
    except (TypeError, ValueError):
        return None
    return PackBalance(
        api_key_id=key,
        pack_id=pack_id,
        credit_usd=max(0.0, credit),
        mode=str(raw.get("mode") or "simulate"),
        updated_at=float(raw.get("updated_at") or 0.0),
    )


def apply_pack(api_key_id: str, pack_id: PackId, *, mode: str = "simulate") -> PackBalance:
    pack = PACKS[pack_id]
    key = (api_key_id or "").strip()
    if not key:
        raise ValueError("api_key_id is required")
    balance = PackBalance(
        api_key_id=key,
        pack_id=pack_id,
        credit_usd=pack.api_credit_usd,
        mode=mode or "simulate",
        updated_at=time.time(),
    )
    state = _load()
    state["balances"][key] = balance.to_dict()
    _save(state)
    return balance


def evaluate_pack(
    api_key_id: str | None,
    billable_tokens: int,
) -> dict[str, Any]:
    """Should this issued key spend pooled hops?

    No attached pack: allowed (existing rate limits still apply).
    Attached pack with remaining estimated credit: allowed.
    Attached pack exhausted: 402 body for the gateway.
    """
    if not api_key_id:
        return {"allowed": True, "pack": None, "reason": "loopback_or_unmetered"}
    balance = get_balance(api_key_id)
    if balance is None:
        return {"allowed": True, "pack": None, "reason": "no_pack"}
    spent = estimated_usd(billable_tokens)
    remaining = round(balance.credit_usd - spent, 6)
    payload = {
        "pack": balance.to_dict(),
        "spent_usd_estimated": spent,
        "remaining_usd_estimated": remaining,
        "priced": False,
        "stuck_next_steps": list(STUCK_NEXT_STEPS),
    }
    if remaining > 0:
        return {"allowed": True, **payload}
    return {
        "allowed": False,
        "error_type": "openvault_pack_exhausted",
        "message": (
            "Experience pack credit is used up. Stay on free hops, "
            "Register then Install a free key, Bring your own key, "
            "or buy a larger pack."
        ),
        **payload,
    }


__all__ = [
    "API_SHARE",
    "PACKS",
    "STUCK_NEXT_STEPS",
    "ExperiencePack",
    "PackBalance",
    "apply_pack",
    "blend_usd_per_1m",
    "estimated_usd",
    "evaluate_pack",
    "get_balance",
    "list_packs",
    "parse_pack_id",
]
