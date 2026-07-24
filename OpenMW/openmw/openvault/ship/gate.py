"""Leave-machine / retrieve / deploy gate — OpenVault is the only allow authority.

Cortex and apps request; OpenVault decides. See PRODUCT_ROLES.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.store import KeyVault

GateAction = Literal["retrieve", "run", "deploy", "leave", "connect"]


@dataclass
class GateDecision:
    """Allow/deny payload for Cortex / AirGPT / OpenIDE clients."""

    allowed: bool
    action: str
    reasons: list[str] = field(default_factory=list)
    keys_ready: bool = False
    locate: dict[str, Any] = field(default_factory=dict)
    required_providers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_gate(
    *,
    action: GateAction,
    vault: KeyVault,
    fallback: FallbackManager | None = None,
    project_path: str = "",
    destination: str = "",
    required_providers: list[str] | None = None,
) -> GateDecision:
    """Gate retrieve/run/deploy/leave/connect against vault readiness."""
    action = (action or "run").lower()  # type: ignore[assignment]
    if action not in ("retrieve", "run", "deploy", "leave", "connect"):
        return GateDecision(
            allowed=False,
            action=str(action),
            reasons=[f"unknown action '{action}'"],
        )

    required = list(required_providers or [])
    if action in ("deploy", "leave") and not required:
        required = ["openai", "anthropic", "openrouter", "groq", "google"]

    keys = vault.list_keys()
    enabled = [k for k in keys if k.enabled and k.lifecycle == "active"]
    by_provider = {k.provider for k in enabled}
    keys_ready = bool(enabled)
    if required:
        keys_ready = any(p in by_provider for p in required) or any(
            k.provider == "custom" for k in enabled
        )

    reasons: list[str] = []
    if not enabled:
        reasons.append("no enabled keys in OpenVault")
    elif required and not keys_ready:
        reasons.append(f"need one of providers: {', '.join(required)}")

    hops: list[dict[str, Any]] = []
    if fallback is not None:
        status = fallback.status()
        hops = list(status.hops)

    locate: dict[str, Any] = {
        "project_path": (project_path or "")[:500],
        "destination": (destination or "")[:200],
        "key_count": len(enabled),
        "providers": sorted(by_provider),
        "fallback_hops": len(hops),
    }

    # Local run/retrieve may proceed with local engines even without cloud keys.
    if action in ("run", "retrieve", "connect") and not keys_ready:
        if "ollama" in by_provider or "cortex" in by_provider or "litellm" in by_provider:
            keys_ready = True
            reasons = [r for r in reasons if not r.startswith("need one of")]
        elif action == "connect":
            # Mesh connect does not require provider keys.
            keys_ready = True
            reasons = []

    if action in ("deploy", "leave") and not keys_ready:
        return GateDecision(
            allowed=False,
            action=action,
            reasons=reasons or ["deploy/leave blocked: vault not ready"],
            keys_ready=False,
            locate=locate,
            required_providers=required,
        )

    if reasons and action in ("deploy", "leave"):
        return GateDecision(
            allowed=False,
            action=action,
            reasons=reasons,
            keys_ready=keys_ready,
            locate=locate,
            required_providers=required,
        )

    return GateDecision(
        allowed=True,
        action=action,
        reasons=reasons or ["gate open"],
        keys_ready=keys_ready or action == "connect",
        locate=locate,
        required_providers=required,
    )
