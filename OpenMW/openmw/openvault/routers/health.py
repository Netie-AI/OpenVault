"""Per-key precheck health history API (CARD_HEALTH_HISTORY H2).

Mounted by ``create_app`` via ``app.include_router(build_health_router(vault))``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from openmw.openvault.vault.health_store import HealthStore, parse_window
from openmw.openvault.vault.quota import quota_view
from openmw.openvault.vault.store import KeyVault


def build_health_router(vault: KeyVault) -> APIRouter:
    """Return a router bound to ``vault`` for ``GET /api/keys/{key_id}/health``."""

    router = APIRouter(tags=["health"])

    @router.get("/api/keys/{key_id}/health")
    def key_health(
        key_id: str,
        window: str = Query(default="24h"),
    ) -> dict[str, object]:
        if vault.get(key_id) is None:
            raise HTTPException(status_code=404, detail="key not found")
        try:
            parse_window(window)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        store = HealthStore(db_path=vault.db_path)
        summary = store.summarize(key_id, window=window)
        payload = store.to_api_dict(summary)
        record = vault.get(key_id)
        if (
            payload["current_status"] is None
            and record is not None
            and record.precheck_status != "unknown"
        ):
            payload["current_status"] = record.precheck_status
        if record is not None:
            view = quota_view(record.provider, vault.db_path)
            if view.status == "quota_exhausted" and payload["current_status"] in (
                None,
                "ok",
                "unknown",
            ):
                payload["current_status"] = view.status
                payload["quota_reset_at"] = view.reset_at
                payload["tokens_used"] = view.tokens_used
                payload["daily_token_limit"] = view.limit
        return payload

    return router
