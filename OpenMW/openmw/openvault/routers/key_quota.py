"""Admin per-key quota view.

``GET /api/keys/quota`` sits under ``/api/keys``, so ``http_guard`` requires
``X-OpenVault-Admin`` before the loopback bypass.
"""

from __future__ import annotations

from fastapi import APIRouter

from openmw.openvault.vault.key_quota import quota_snapshot
from openmw.openvault.vault.store import KeyVault


def build_key_quota_router(vault: KeyVault) -> APIRouter:
    router = APIRouter(tags=["keys"])

    @router.get("/api/keys/quota")
    def key_quota() -> dict[str, object]:
        return quota_snapshot(vault)

    return router
