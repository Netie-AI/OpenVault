"""FreeRoute tool surface: register deep-links + spendable catalog + bind kids.

Mounted by ``create_app`` via ``build_freeroute_router(vault, fallback)``.
Register URLs come from the provider catalog -- this is not the Marketing
netie.ai /rates page. JWKS kids are public pin material; mint stays loopback.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.providers import get_provider, list_catalog, spendable_for_freeroute
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.trust import TrustStore


def build_freeroute_router(vault: KeyVault, fallback: FallbackManager) -> APIRouter:
    router = APIRouter(tags=["freeroute"])

    @router.get("/api/tool/register")
    def tool_register(provider: str = "") -> dict[str, Any]:
        """Deep-link payload for account registration (catalog register_url)."""
        if not provider.strip():
            rows = [_register_row(row) for row in list_catalog(free_only=True)]
            return {
                "ok": True,
                "providers": rows,
                "count": len(rows),
                "return_path": "/keys#free",
                "help": (
                    "Open /tool/register?provider=<id> then install the copied key. "
                    "Not the public /rates page."
                ),
            }
        spec = get_provider(provider.strip())
        if spec is None:
            raise HTTPException(status_code=404, detail="unknown provider in catalog")
        return {"ok": True, **_register_row(spec.to_dict())}

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
