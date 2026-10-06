"""FreeRoute role routing, price table, and hard spend caps (ROUTE-ROLE-BUDGET-01).

One policy file, loaded per request so an operator edit applies without a
restart:

* ``route_policy.json`` next to this module holds the shipped defaults.
* ``OPENVAULT_ROUTE_POLICY`` names an operator file layered on top. A role list
  in it replaces that role's list; ``models`` entries are checked before the
  defaults; ``caps.keys`` / ``caps.callers`` entries merge by id.

Three rules the gateway relies on:

* **A model is paid unless the policy marks it free.** There is no third
  state. An unpriced paid model costs ``None`` - unknown, never a guessed zero.
* **Paid needs a USD cap.** On a role-routed request a paid hop is refused
  unless the vault key that would spend, or the caller, has a daily or monthly
  USD cap. Requests without ``role`` keep today's pool walk; any cap that
  applies to them is still enforced.
* **A cap that would be exceeded refuses.** It never walks on to a paid model
  to find room. Spend is the sum of ``usage_events.est_cost_usd`` in keys.db
  plus what other in-flight requests in this process have reserved.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Protocol

import structlog

log = structlog.get_logger()

ROUTE_ROLES: tuple[str, ...] = ("sql", "tool", "reason", "summarize")
POLICY_ENV = "OPENVAULT_ROUTE_POLICY"
DEFAULT_POLICY_PATH = Path(__file__).with_name("route_policy.json")

BUDGET_EXCEEDED = "budget_exceeded"
BUDGET_ERROR_TYPE = "openvault_budget_exceeded"
UNKNOWN_ROLE_TYPE = "openvault_unknown_role"
ROLE_CONFLICT_TYPE = "openvault_role_conflict"
POLICY_INVALID_TYPE = "openvault_route_policy_invalid"

_SCOPE_KEY = "key"
_SCOPE_CALLER = "caller"


class RoutePolicyError(ValueError):
    """The policy file cannot be read. Callers fail closed on this."""


@dataclass(frozen=True)
class RoleHop:
    provider: str
    model: str


@dataclass(frozen=True)
class ModelPrice:
    free: bool
    usd_per_1m_in: float | None = None
    usd_per_1m_out: float | None = None

    def cost(self, tokens_in: int, tokens_out: int) -> float | None:
        """USD for this many tokens. None when a paid model has no price."""
        if self.free:
            return 0.0
        if self.usd_per_1m_in is None or self.usd_per_1m_out is None:
            return None
        usd = (
            max(0, tokens_in) * self.usd_per_1m_in + max(0, tokens_out) * self.usd_per_1m_out
        ) / 1_000_000.0
        return round(usd, 8)


PAID_UNPRICED = ModelPrice(free=False)


@dataclass(frozen=True)
class SpendCap:
    daily_usd: float | None = None
    monthly_usd: float | None = None
    max_tokens_per_request: int | None = None

    @property
    def has_usd_cap(self) -> bool:
        return self.daily_usd is not None or self.monthly_usd is not None


@dataclass(frozen=True)
class RoutePolicy:
    roles: dict[str, tuple[RoleHop, ...]]
    #: Ordered ``(pattern, price)``; operator entries first. Exact beats glob.
    models: tuple[tuple[str, ModelPrice], ...]
    key_caps: dict[str, SpendCap]
    caller_caps: dict[str, SpendCap]
    max_tokens_per_request: int | None
    sources: tuple[str, ...] = ()

    def price_for(self, provider: str, model: str) -> ModelPrice:
        name = f"{provider}/{model}"
        for pattern, price in self.models:
            if pattern == name:
                return price
        for pattern, price in self.models:
            if fnmatchcase(name, pattern):
                return price
        return PAID_UNPRICED

    def hops_for(self, role: str) -> tuple[RoleHop, ...]:
        return self.roles.get(role, ())


def _non_negative(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RoutePolicyError(f"{where} must be a number or null")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise RoutePolicyError(f"{where} must be finite and >= 0")
    return number


def _positive_int(value: Any, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RoutePolicyError(f"{where} must be a positive integer or null")
    return value


def _parse_roles(raw: Any, where: str) -> dict[str, tuple[RoleHop, ...]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RoutePolicyError(f"{where}.roles must be an object")
    out: dict[str, tuple[RoleHop, ...]] = {}
    for role, hops in raw.items():
        if role not in ROUTE_ROLES:
            raise RoutePolicyError(f"{where}.roles.{role} is not one of {list(ROUTE_ROLES)}")
        if not isinstance(hops, list):
            raise RoutePolicyError(f"{where}.roles.{role} must be a list")
        parsed: list[RoleHop] = []
        for hop in hops:
            provider = hop.get("provider") if isinstance(hop, dict) else None
            model = hop.get("model") if isinstance(hop, dict) else None
            if not isinstance(provider, str) or not isinstance(model, str):
                raise RoutePolicyError(f"{where}.roles.{role} entries need provider and model")
            if not provider.strip() or not model.strip():
                raise RoutePolicyError(f"{where}.roles.{role} entries need provider and model")
            parsed.append(RoleHop(provider=provider.strip(), model=model.strip()))
        out[role] = tuple(parsed)
    return out


def _parse_models(raw: Any, where: str) -> list[tuple[str, ModelPrice]]:
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise RoutePolicyError(f"{where}.models must be an object")
    out: list[tuple[str, ModelPrice]] = []
    for pattern, entry in raw.items():
        if not isinstance(entry, dict):
            raise RoutePolicyError(f"{where}.models.{pattern} must be an object")
        free = entry.get("free", False)
        if not isinstance(free, bool):
            raise RoutePolicyError(f"{where}.models.{pattern}.free must be true or false")
        out.append(
            (
                str(pattern),
                ModelPrice(
                    free=free,
                    usd_per_1m_in=_non_negative(
                        entry.get("usd_per_1m_in"), f"{where}.models.{pattern}.usd_per_1m_in"
                    ),
                    usd_per_1m_out=_non_negative(
                        entry.get("usd_per_1m_out"), f"{where}.models.{pattern}.usd_per_1m_out"
                    ),
                ),
            )
        )
    return out


def _parse_cap(raw: Any, where: str) -> SpendCap:
    if not isinstance(raw, dict):
        raise RoutePolicyError(f"{where} must be an object")
    return SpendCap(
        daily_usd=_non_negative(raw.get("daily_usd"), f"{where}.daily_usd"),
        monthly_usd=_non_negative(raw.get("monthly_usd"), f"{where}.monthly_usd"),
        max_tokens_per_request=_positive_int(
            raw.get("max_tokens_per_request"), f"{where}.max_tokens_per_request"
        ),
    )


def _parse_cap_map(raw: Any, where: str) -> dict[str, SpendCap]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RoutePolicyError(f"{where} must be an object")
    return {str(ident): _parse_cap(cap, f"{where}.{ident}") for ident, cap in raw.items()}


def _read_layer(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RoutePolicyError(f"cannot read route policy {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RoutePolicyError(f"route policy {path.name} must be a JSON object")
    return raw


def load_route_policy() -> RoutePolicy:
    """Shipped defaults plus the ``OPENVAULT_ROUTE_POLICY`` file, if set."""
    paths = [DEFAULT_POLICY_PATH]
    override = (os.environ.get(POLICY_ENV) or "").strip()
    if override:
        paths.append(Path(override))
    roles: dict[str, tuple[RoleHop, ...]] = {}
    models: list[tuple[str, ModelPrice]] = []
    key_caps: dict[str, SpendCap] = {}
    caller_caps: dict[str, SpendCap] = {}
    max_tokens: int | None = None
    for path in paths:
        layer = _read_layer(path)
        where = path.name
        roles.update(_parse_roles(layer.get("roles"), where))
        models = _parse_models(layer.get("models"), where) + models
        caps = layer.get("caps")
        if caps is None:
            caps = {}
        if not isinstance(caps, dict):
            raise RoutePolicyError(f"{where}.caps must be an object")
        key_caps.update(_parse_cap_map(caps.get("keys"), f"{where}.caps.keys"))
        caller_caps.update(_parse_cap_map(caps.get("callers"), f"{where}.caps.callers"))
        if "max_tokens_per_request" in caps:
            max_tokens = _positive_int(
                caps.get("max_tokens_per_request"), f"{where}.caps.max_tokens_per_request"
            )
    return RoutePolicy(
        roles=roles,
        models=tuple(models),
        key_caps=key_caps,
        caller_caps=caller_caps,
        max_tokens_per_request=max_tokens,
        sources=tuple(str(p) for p in paths),
    )


def parse_route_role(value: object) -> str | None:
    """``None`` keeps today's routing. Anything outside ROUTE_ROLES raises."""
    if value is None:
        return None
    if isinstance(value, str) and value in ROUTE_ROLES:
        return value
    raise ValueError(value)


def unknown_role_body(value: object) -> dict[str, Any]:
    return {
        "error": {
            "message": f"role must be one of {', '.join(ROUTE_ROLES)}, or omitted",
            "type": UNKNOWN_ROLE_TYPE,
            "role": value if isinstance(value, str | int | float | bool) else str(value),
            "allowed": list(ROUTE_ROLES),
        },
        **empty_stamps(),
    }


def role_conflict_body(flag: str) -> dict[str, Any]:
    return {
        "error": {
            "message": f"role picks the model list; it cannot be combined with {flag}",
            "type": ROLE_CONFLICT_TYPE,
            "conflicts_with": flag,
        },
        **empty_stamps(),
    }


def policy_invalid_body(exc: RoutePolicyError) -> dict[str, Any]:
    return {
        "error": {"message": str(exc), "type": POLICY_INVALID_TYPE},
        **empty_stamps(),
    }


def empty_stamps() -> dict[str, Any]:
    """Nothing served, nothing spent."""
    return {
        "model": None,
        "provider": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "est_cost_usd": 0.0,
    }


@dataclass(frozen=True)
class BudgetBlock:
    """Which cap refused the request, and by how much."""

    #: ``key`` | ``caller`` | ``request`` | ``paid_default``
    scope: str
    #: ``daily_usd`` | ``monthly_usd`` | ``max_tokens_per_request`` | ``paid_requires_cap``
    limit_name: str
    limit: float | int
    spent: float | int | None
    requested: float | int | None
    #: Caller id, or the first 8 characters of a vault key id. Never a secret.
    cap_id: str = ""
    provider: str = ""
    model: str = ""

    @property
    def cap(self) -> str:
        if self.scope == "paid_default":
            return "paid_default"
        return f"{self.scope}.{self.limit_name}"

    def message(self) -> str:
        if self.scope == "paid_default":
            return (
                f"{self.provider}/{self.model} is a paid model and no USD cap is set for "
                "this key or caller; paid models stay off until one is"
            )
        if self.scope == "request":
            return f"request asks for {self.requested} output tokens; the cap is {self.limit}"
        if self.requested is None:
            return (
                f"{self.provider}/{self.model} has no price in the route policy, so "
                f"{self.cap} cannot be checked"
            )
        return (
            f"{self.cap} is {self.limit} USD; {self.spent} spent, "
            f"this request needs ~{self.requested}"
        )

    def to_body(self) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message(),
                "type": BUDGET_ERROR_TYPE,
                "reason": BUDGET_EXCEEDED,
                "cap": self.cap,
                "cap_detail": {
                    "scope": self.scope,
                    "id": self.cap_id,
                    "limit_name": self.limit_name,
                    "limit": self.limit,
                    "spent": self.spent,
                    "requested": self.requested,
                    "unit": "tokens" if self.scope == "request" else "usd",
                    "provider": self.provider or None,
                    "model": self.model or None,
                },
            },
            **empty_stamps(),
            "served_provider": None,
            "served_model": None,
            "served_local": False,
        }


class SpendLedger(Protocol):
    def spend_usd(
        self, *, vault_key_id: str | None = None, service_id: str | None = None, since: float
    ) -> float: ...


def day_start(now: float) -> float:
    current = datetime.fromtimestamp(now, timezone.utc)
    return current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def month_start(now: float) -> float:
    current = datetime.fromtimestamp(now, timezone.utc)
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


_PENDING_LOCK = threading.Lock()
#: USD reserved by in-flight hops in this process, by ``scope:id``.
_PENDING: dict[str, float] = {}


def reset_pending() -> None:
    with _PENDING_LOCK:
        _PENDING.clear()


@dataclass
class SpendGuard:
    """Per-request budget state: which caps apply, and what this hop holds."""

    policy: RoutePolicy
    ledger: SpendLedger
    #: Credential-bound for issued keys (the key id); ``service_id`` for loopback.
    caller_id: str
    route_role: str | None = None
    clock: Callable[[], float] = time.time
    blocks: list[BudgetBlock] = field(default_factory=list)
    _held_scopes: tuple[str, ...] = ()
    _held_usd: float = 0.0
    _held: bool = False

    @property
    def max_tokens_cap(self) -> int | None:
        caps = [self.policy.max_tokens_per_request]
        caller = self.policy.caller_caps.get(self.caller_id)
        if caller is not None:
            caps.append(caller.max_tokens_per_request)
        found = [c for c in caps if c is not None]
        return min(found) if found else None

    def check_request_tokens(self, requested: int | None) -> BudgetBlock | None:
        cap = self.max_tokens_cap
        if cap is None or requested is None or requested <= cap:
            return None
        caller = self.policy.caller_caps.get(self.caller_id)
        per_caller = caller is not None and caller.max_tokens_per_request == cap
        return BudgetBlock(
            scope="request",
            limit_name="max_tokens_per_request",
            limit=cap,
            spent=None,
            requested=requested,
            cap_id=self.caller_id if per_caller else "",
        )

    def _applicable(self, key_id: str) -> list[tuple[str, str, str, SpendCap]]:
        found: list[tuple[str, str, str, SpendCap]] = []
        key_cap = self.policy.key_caps.get(key_id) if key_id else None
        if key_cap is not None and key_cap.has_usd_cap:
            found.append((_SCOPE_KEY, key_id, key_id[:8], key_cap))
        caller_cap = self.policy.caller_caps.get(self.caller_id)
        if caller_cap is not None and caller_cap.has_usd_cap:
            found.append((_SCOPE_CALLER, self.caller_id, self.caller_id, caller_cap))
        return found

    def _spent(self, scope: str, ident: str, since: float) -> float:
        if scope == _SCOPE_KEY:
            return self.ledger.spend_usd(vault_key_id=ident, since=since)
        return self.ledger.spend_usd(service_id=ident, since=since)

    def admit(
        self,
        *,
        provider: str,
        model: str,
        key_id: str,
        prompt_tokens: int,
        max_output: int,
    ) -> BudgetBlock | None:
        """Reserve this hop's worst-case cost, or say which cap refuses it."""
        self.release()
        price = self.policy.price_for(provider, model)
        if price.free:
            return None
        caps = self._applicable(key_id)
        if not caps:
            if self.route_role is None:
                return None
            block = BudgetBlock(
                scope="paid_default",
                limit_name="paid_requires_cap",
                limit=0.0,
                spent=None,
                requested=price.cost(prompt_tokens, max_output),
                provider=provider,
                model=model,
            )
            self.blocks.append(block)
            return block
        estimate = price.cost(prompt_tokens, max_output)
        now = self.clock()
        with _PENDING_LOCK:
            for scope, ident, shown, cap in caps:
                pending = _PENDING.get(f"{scope}:{ident}", 0.0)
                for limit_name, limit, since in (
                    ("daily_usd", cap.daily_usd, day_start(now)),
                    ("monthly_usd", cap.monthly_usd, month_start(now)),
                ):
                    if limit is None:
                        continue
                    spent = round(self._spent(scope, ident, since) + pending, 8)
                    if estimate is None or spent + estimate > limit:
                        block = BudgetBlock(
                            scope=scope,
                            limit_name=limit_name,
                            limit=limit,
                            spent=spent,
                            requested=estimate,
                            cap_id=shown,
                            provider=provider,
                            model=model,
                        )
                        self.blocks.append(block)
                        return block
            held = tuple(f"{scope}:{ident}" for scope, ident, _shown, _cap in caps)
            for name in held:
                _PENDING[name] = _PENDING.get(name, 0.0) + float(estimate or 0.0)
        self._held_scopes = held
        self._held_usd = float(estimate or 0.0)
        self._held = True
        return None

    def release(self) -> None:
        """Drop this request's reservation. Call after the ledger row is written."""
        if not self._held:
            return
        with _PENDING_LOCK:
            for name in self._held_scopes:
                left = _PENDING.get(name, 0.0) - self._held_usd
                if left <= 1e-12:
                    _PENDING.pop(name, None)
                else:
                    _PENDING[name] = left
        self._held_scopes = ()
        self._held_usd = 0.0
        self._held = False

    def cost_for(
        self, provider: str, model: str, tokens_in: int | None, tokens_out: int | None
    ) -> float | None:
        """What the hop that served cost. 0.0 when nothing served."""
        if not provider:
            return 0.0
        price = self.policy.price_for(provider, model)
        if price.free:
            return 0.0
        if tokens_in is not None and tokens_out is not None:
            measured = price.cost(tokens_in, tokens_out)
            if measured is not None:
                return measured
        # No usage frame: bill the worst case that was reserved, never less.
        return self._held_usd if self._held else None

    def stamps(
        self,
        *,
        provider: str,
        model: str,
        tokens_in: int | None,
        tokens_out: int | None,
    ) -> dict[str, Any]:
        if not provider:
            return empty_stamps()
        return {
            "model": model or None,
            "provider": provider,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "est_cost_usd": self.cost_for(provider, model, tokens_in, tokens_out),
        }


def usage_pair(usage: object) -> tuple[int | None, int | None]:
    """``(prompt_tokens, completion_tokens)`` from an upstream usage block."""
    if not isinstance(usage, dict):
        return None, None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if isinstance(prompt, bool) or isinstance(completion, bool):
        return None, None
    if isinstance(prompt, int | float) and isinstance(completion, int | float):
        return int(prompt), int(completion)
    return None, None


__all__ = [
    "BUDGET_ERROR_TYPE",
    "BUDGET_EXCEEDED",
    "DEFAULT_POLICY_PATH",
    "POLICY_ENV",
    "POLICY_INVALID_TYPE",
    "ROLE_CONFLICT_TYPE",
    "ROUTE_ROLES",
    "UNKNOWN_ROLE_TYPE",
    "BudgetBlock",
    "ModelPrice",
    "RoleHop",
    "RoutePolicy",
    "RoutePolicyError",
    "SpendCap",
    "SpendGuard",
    "SpendLedger",
    "empty_stamps",
    "load_route_policy",
    "parse_route_role",
    "policy_invalid_body",
    "reset_pending",
    "role_conflict_body",
    "unknown_role_body",
    "usage_pair",
]
