"""FreeRoute tool surface: register deep-links + spendable catalog + bind kids.

Mounted by ``create_app`` via ``build_freeroute_router(vault, fallback)``.
Register URLs come from the provider catalog -- this is not the Marketing
netie.ai /rates page. JWKS kids are public pin material; mint stays loopback.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.free_keys_onboard import (
    RETIRED_ONBOARD_IDS,
    onboard_item,
    onboard_payload,
    sort_groq_first,
)
from openmw.openvault.vault.providers import get_provider, list_catalog, spendable_for_freeroute
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.trust import TrustStore


def build_freeroute_router(vault: KeyVault, fallback: FallbackManager) -> APIRouter:
    router = APIRouter(tags=["freeroute"])

    @router.get("/api/tool/register")
    def tool_register(provider: str = "") -> dict[str, Any]:
        """Deep-link payload for account registration (catalog register_url)."""
        if not provider.strip():
            rows = _register_catalog_rows(list_catalog(free_only=True))
            return {
                "ok": True,
                "providers": rows,
                "count": len(rows),
                "return_path": "/keys#free",
                "help": (
                    "Open /tool/register?provider=<id> then install the copied key. "
                    "Groq first. GitHub Models retired. Not the public /rates page."
                ),
            }
        want = provider.strip()
        if want in RETIRED_ONBOARD_IDS:
            raise HTTPException(
                status_code=404,
                detail="GitHub Models inference is retired — it is not on the Get free keys list",
            )
        spec = get_provider(want)
        if spec is not None:
            row = _register_row(spec.to_dict())
            item = onboard_item(want)
            if item is not None:
                row["register_url"] = item.register_url
                row["add_key_provider"] = item.add_key_provider
                row["default_base_url"] = item.default_base_url
                row["needs_account_id"] = item.needs_account_id
            return {"ok": True, **row}
        item = onboard_item(want)
        if item is None:
            raise HTTPException(status_code=404, detail="unknown provider in catalog")
        return {"ok": True, **item.to_dict(), "provider": item.id, "name": item.label}

    @router.get("/api/freeroute/onboard")
    def freeroute_onboard() -> dict[str, Any]:
        """Groq-first Get free keys checklist. Read-only; mint stays loopback."""
        return onboard_payload(vault.list_keys())

    @router.get("/api/freeroute/status")
    def freeroute_status() -> dict[str, Any]:
        """Operator snapshot: spendable hops, vault seal, public JWKS kids."""
        spendable = spendable_for_freeroute()
        hops = fallback.status().hops
        jwks = TrustStore().jwks()
        kids = [str(k.get("kid") or "") for k in jwks.get("keys", []) if k.get("kid")]
        sealed = bool(vault.seal.is_sealed)
        pooled = list(vault.pooled_ordered()) if not sealed else []
        return {
            "ok": True,
            "surface": "freeroute",
            "sealed": sealed,
            "public_bind": False,
            "jwks_uri": "/.well-known/jwks.json",
            "jwks_alt": "/keys/jwks",
            "kids": kids,
            "kid_count": len(kids),
            "pooled_key_count": len(pooled),
            "hops": hops,
            "spendable": spendable,
            "spendable_count": len(spendable),
            "usage_unit_status": "NEEDS-YOU",
            "onboard_path": "/keys#free",
        }

    return router


def _register_row(row: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(row["id"])
    chat_models = [str(m) for m in (row.get("chat_models") or [])]
    openai_compatible = bool(row.get("openai_compatible", True))
    return {
        "provider": provider_id,
        "id": provider_id,
        "name": str(row.get("name") or provider_id),
        "tier": str(row.get("tier") or ""),
        "register_url": str(row.get("register_url") or ""),
        "docs_url": str(row.get("docs_url") or ""),
        "free_notes": str(row.get("free_notes") or ""),
        "deep_link": f"/tool/register?provider={provider_id}",
        "return_path": f"/keys?provider={provider_id}#free",
        "spendable": openai_compatible and bool(chat_models),
        "chat_models": chat_models,
        "openai_compatible": openai_compatible,
    }


def _register_catalog_rows(catalog_free: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in catalog_free:
        pid = str(raw.get("id") or "")
        if not pid or pid in RETIRED_ONBOARD_IDS:
            continue
        row = _register_row(raw)
        item = onboard_item(pid)
        if item is not None:
            row["register_url"] = item.register_url
            row["add_key_provider"] = item.add_key_provider
            row["default_base_url"] = item.default_base_url
            row["needs_account_id"] = item.needs_account_id
            row["free_notes"] = item.notes or row.get("free_notes") or ""
        rows.append(row)
        seen.add(pid)
    cf = onboard_item("cloudflare")
    if cf is not None and cf.id not in seen:
        rows.append(cf.to_dict() | {"provider": cf.id, "name": cf.label})
    return sort_groq_first(rows)
