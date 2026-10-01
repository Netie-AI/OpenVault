"""Provider cards page and the POST-only test-and-add route.

GET /provider-cards renders the cards. POST /api/keys/cards reuses
``key_add.add_tested_key``. The provider key is not logged and not returned.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from openmw.openvault.vault.key_add import add_tested_key
from openmw.openvault.vault.provider_cards import (
    add_response,
    cards_payload,
    icon_path,
    render_cards_page,
)
from openmw.openvault.vault.store import KeyVault


def build_provider_cards_router(vault: KeyVault) -> APIRouter:
    router = APIRouter(tags=["provider-cards"])

    @router.get("/provider-cards")
    def provider_cards_page() -> HTMLResponse:
        return HTMLResponse(render_cards_page())

    @router.get("/provider-cards/icons/{icon_name}")
    def provider_card_icon(icon_name: str) -> Response:
        path = icon_path(icon_name)
        if path is None:
            return JSONResponse(status_code=404, content={"detail": "not found"})
        return FileResponse(path, media_type="image/svg+xml")

    @router.get("/api/providers/cards")
    def provider_cards_json() -> dict[str, object]:
        return dict(cards_payload())

    @router.post("/api/keys/cards")
    async def provider_cards_add(request: Request) -> JSONResponse:
        raw = await request.body()
        provider, secret = _read_body(raw)
        del raw
        result = add_tested_key(vault, provider, secret)
        status = 200 if result.ok or result.duplicate else 400
        return JSONResponse(status_code=status, content=add_response(result))

    return router


def _read_body(raw: bytes) -> tuple[str, str]:
    """Provider id and secret. The raw body is not logged."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    provider = payload.get("provider")
    secret = payload.get("secret")
    if not isinstance(provider, str):
        provider = ""
    if not isinstance(secret, str):
        secret = ""
    return provider, secret
