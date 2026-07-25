"""AirGPT / OpenIDE Key Vault snapshot — OpenVault is SoT for keys.

Maps provider catalog + vault records into the AirGPT popup shape so shells
stay thin clients (PRODUCT_ROLES.md).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from openmw.openvault.vault.providers import list_catalog
from openmw.openvault.vault.store import KeyRecord, KeyVault, ProviderKind

# AirGPT env_key → OpenVault ProviderKind (custom for CLI/deploy extras)
ENV_KEY_TO_PROVIDER: dict[str, ProviderKind] = {
    "NETIE_ENGINE_KEY": "cortex",
    "CURSOR_API_KEY": "custom",
    "OMNIROUTE_API_KEY": "custom",
    "GROQ_API_KEY": "groq",
    "OPENROUTER_API_KEY": "openrouter",
    "GOOGLE_API_KEY": "google",
    "DEEPSEEK_API_KEY": "deepseek",
    "SEA_LION_API_KEY": "custom",
    "ANTHROPIC_API_KEY": "anthropic",
    "OPENAI_API_KEY": "openai",
    "OLLAMA_API_KEY": "ollama",
    "VLLM_API_KEY": "custom",
    "GH_TOKEN": "custom",
    "GITHUB_TOKEN": "custom",
    "OPENSHIP_API_TOKEN": "custom",
    "OPENVAULT_URL": "custom",
}

PROVIDER_TO_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "google": "GOOGLE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "cortex": "NETIE_ENGINE_KEY",
    "huggingface": "HF_TOKEN",
    "mistral": "MISTRAL_API_KEY",
    "together": "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "litellm": "LITELLM_API_KEY",
    "github_models": "GITHUB_TOKEN",
    "siliconflow": "SILICONFLOW_API_KEY",
}


def _best_key(keys: list[KeyRecord], provider: str) -> KeyRecord | None:
    matches = [k for k in keys if k.provider == provider and k.enabled and k.lifecycle == "active"]
    if not matches:
        return None
    matches.sort(key=lambda k: (k.priority, k.updated_at))
    return matches[0]


def keyvault_snapshot(vault: KeyVault, *, openvault_url: str = "") -> dict[str, Any]:
    """Catalog + configured status for AirGPT Key Vault UI. No raw secrets."""
    keys = vault.list_keys()
    providers: list[dict[str, Any]] = []
    for spec in list_catalog():
        pid = str(spec.get("id") or "")
        rec = _best_key(keys, pid)
        env_key = PROVIDER_TO_ENV.get(pid, f"{pid.upper()}_API_KEY")
        tier = str(spec.get("tier") or "")
        group = "local" if tier == "local" else ("gateway" if pid in ("litellm",) else "cloud")
        register = str(spec.get("register_url") or "")
        providers.append(
            {
                "id": pid,
                "label": spec.get("name") or pid,
                "group": group,
                "env_key": env_key,
                "alt_env_keys": [],
                "console": register,
                "signup": register,
                "prefix": "",
                "free_note": spec.get("free_notes") or tier,
                "steps": [
                    f"Open {register}" if register else "Configure in OpenVault",
                    "Paste key in OpenVault console (or AirGPT Key Vault → saves here)",
                    "Precheck runs automatically when online",
                ],
                "configured": rec is not None,
                "masked": rec.masked_secret if rec else "",
                "openvault_key_id": rec.id if rec else None,
                "base_url": (rec.base_url if rec else "") or str(spec.get("base_url") or ""),
                "needed_by": list(spec.get("needed_by") or []),
            }
        )

    # Extra AirGPT cards not in OV catalog (CLI / OpenFree / OpenShip) as custom slots
    extras = (
        (
            "omniroute",
            "OpenFree",
            "OMNIROUTE_API_KEY",
            "gateway",
            (
                "OpenFree (ours) — OmniRoute-inspired free gateway; "
                "AirGPT enables it, OpenVault routes/custody"
            ),
        ),
        (
            "openship",
            "OpenShip (OpenVault)",
            "OPENSHIP_API_TOKEN",
            "deploy",
            "OpenShip is implemented in OpenVault ship/ — optional external URL/token",
        ),
        (
            "github_cli",
            "GitHub CLI token",
            "GH_TOKEN",
            "cli",
            "gh auth status / repo scope (login is out-of-band)",
        ),
        ("cursor", "Cursor SDK", "CURSOR_API_KEY", "cloud", "OpenIDE Cursor backends"),
    )
    for pid, label, env_key, group, note in extras:
        rec = next(
            (
                k
                for k in keys
                if k.enabled
                and k.lifecycle == "active"
                and (
                    k.label.lower().startswith(pid)
                    or (k.provider == "custom" and pid in k.label.lower())
                )
            ),
            None,
        )
        providers.append(
            {
                "id": pid,
                "label": label,
                "group": group,
                "env_key": env_key,
                "alt_env_keys": [],
                "console": "",
                "signup": "",
                "prefix": "",
                "free_note": note,
                "steps": ["Save via OpenVault or AirGPT Key Vault (proxied here)"],
                "configured": rec is not None,
                "masked": rec.masked_secret if rec else "",
                "openvault_key_id": rec.id if rec else None,
            }
        )

    return {
        "ok": True,
        "source": "openvault",
        "openvault_url": openvault_url,
        "providers": providers,
        "policy": (
            "OpenVault Key Vault — single custody for keys, gateways, and CLI tokens. "
            "AirGPT/OpenIDE link here; env.local is an offline cache only. "
            "Account signup stays a human step."
        ),
        "product_name": "OpenVault Key Vault",
        "save_endpoint": "/api/keyvault/upsert",
        "free_fallback_order": ["omniroute", "groq", "openrouter", "google"],
        "keys_count": len([k for k in keys if k.enabled]),
    }


def upsert_env_secret(
    vault: KeyVault,
    *,
    env_key: str,
    secret: str,
    label: str = "",
    base_url: str = "",
    provider_hint: str = "",
) -> dict[str, Any]:
    """Create or rotate a key from an AirGPT-style env_key + secret."""
    secret = (secret or "").strip()
    if not secret or "…" in secret or secret.startswith("••"):
        return {"ok": False, "error": "empty or masked secret"}

    env_key = (env_key or "").strip().upper()
    provider: ProviderKind = ENV_KEY_TO_PROVIDER.get(env_key, "custom")
    if provider_hint and provider_hint in (
        "openai",
        "anthropic",
        "openrouter",
        "groq",
        "google",
        "mistral",
        "deepseek",
        "together",
        "fireworks",
        "cerebras",
        "huggingface",
        "ollama",
        "cortex",
        "litellm",
        "github_models",
        "siliconflow",
        "custom",
    ):
        provider = provider_hint  # type: ignore[assignment]

    label = (label or env_key or provider).strip()[:80]
    existing = [
        k
        for k in vault.list_keys()
        if k.provider == provider
        and k.enabled
        and k.lifecycle == "active"
        and (k.label == label or (provider != "custom" and k.provider == provider))
    ]
    if existing and provider != "custom":
        # Prefer exact label, else lowest priority active
        existing.sort(key=lambda k: (0 if k.label == label else 1, k.priority))
        rec = vault.update(existing[0].id, secret=secret, base_url=base_url or None, label=label)
        if rec is None:
            return {"ok": False, "error": "update failed"}
        return {"ok": True, "action": "updated", "key": asdict(rec)}

    if existing and provider == "custom":
        for k in existing:
            if k.label == label or env_key.lower() in k.label.lower():
                rec = vault.update(k.id, secret=secret, base_url=base_url or None, label=label)
                if rec is None:
                    return {"ok": False, "error": "update failed"}
                return {"ok": True, "action": "updated", "key": asdict(rec)}

    rec = vault.create(
        label=label,
        provider=provider,
        secret=secret,
        role="backup",
        base_url=base_url,
        priority=100,
        enabled=True,
    )
    return {"ok": True, "action": "created", "key": asdict(rec)}
