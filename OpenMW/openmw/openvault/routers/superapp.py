"""Super-app plan: auto-detect route, ship, model, and cloud.

Mounted by the integrator. The plan lists provider ids only. Secrets stay
in KeyVault.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openmw.openvault.ship.hosts import ADAPTERS
from openmw.openvault.superapp.plan import SuperappInputError, plan_superapp
from openmw.openvault.vault.store import KeyVault

_CREDENTIAL_PROVIDERS = {
    str(getattr(adapter, "credential_provider", ""))
    for adapter in ADAPTERS.values()
} - {""}


class PlanBody(BaseModel):
    text: str = ""
    project_path: str = ""


def keyed_provider_ids(vault: KeyVault) -> set[str]:
    """Enabled provider ids. Metadata only; secret blobs are not read.

    Cloud credentials are often stored as provider ``custom`` with the host
    name in the label (Cloudflare, Netlify, Coolify). Those labels count.
    """
    ids: set[str] = set()
    for record in vault.list_keys():
        if not record.enabled or record.lifecycle != "active":
            continue
        ids.add(str(record.provider))
        label = (record.label or "").lower()
        for name in _CREDENTIAL_PROVIDERS:
            if name in label:
                ids.add(name)
    return ids


def build_superapp_router(vault: KeyVault) -> APIRouter:
    router = APIRouter(tags=["superapp"])

    @router.post("/api/superapp/plan")
    def api_superapp_plan(body: PlanBody) -> dict[str, Any]:
        try:
            return plan_superapp(
                body.text,
                project_path=body.project_path,
                keyed_providers=keyed_provider_ids(vault),
            )
        except SuperappInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
