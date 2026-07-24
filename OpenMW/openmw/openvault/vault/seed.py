"""Seed OpenVault with Cortex / AirGPT essential provider slots."""

from __future__ import annotations

from typing import Any, cast

from openmw.openvault.vault.providers import PROVIDER_CATALOG, essentials_for
from openmw.openvault.vault.store import KeyRole, KeyVault, ProviderKind


def seed_essentials(
    vault: KeyVault,
    *,
    consumers: tuple[str, ...] = ("cortex", "airgpt", "openvault"),
    include_local_placeholders: bool = True,
) -> dict[str, Any]:
    """Ensure vault has a slot for every essential provider.

    Does **not** invent cloud secrets. Local providers (ollama/cortex/litellm)
    get placeholder secrets so precheck/downtime can run immediately.
    Cloud providers missing from the vault are listed with register links.
    """
    existing = {k.provider for k in vault.list_keys()}
    created: list[dict[str, str]] = []
    pending_register: list[dict[str, str]] = []

    needed = essentials_for(*consumers)
    needed_ids = {row["id"] for row in needed}

    for spec in PROVIDER_CATALOG:
        if spec.id not in needed_ids:
            continue
        if spec.id in existing:
            continue
        if spec.tier == "local" and include_local_placeholders:
            kind = cast(ProviderKind, spec.id)
            role = cast(KeyRole, spec.default_role)
            record = vault.create(
                label=f"{spec.name} (seeded)",
                provider=kind,
                secret=spec.placeholder_secret or f"{spec.id}-local",
                role=role,
                base_url=spec.base_url,
                priority=50 if spec.default_role == "primary" else 100,
            )
            created.append({"id": record.id, "provider": spec.id, "label": record.label})
            existing.add(kind)
        else:
            pending_register.append(
                {
                    "provider": spec.id,
                    "name": spec.name,
                    "register_url": spec.register_url,
                    "docs_url": spec.docs_url,
                    "tier": spec.tier,
                    "free_notes": spec.free_notes,
                    "needed_by": ",".join(spec.needed_by),
                }
            )

    return {
        "created_local": created,
        "pending_register": pending_register,
        "vault_providers": sorted(existing),
        "message": (
            "Local placeholders seeded. Register cloud keys via register_url, "
            "then POST /api/keys and run precheck-all."
        ),
    }
