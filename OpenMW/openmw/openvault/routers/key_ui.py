"""Friendly key UI routes -- subscribe Cortex issue + copy. No hop spend."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from openmw.openvault.vault.accounts import AccountStore
from openmw.openvault.vault.cortex_key import issue_cortex_key, issued_payload
from openmw.openvault.vault.key_ui_copy import (
    BYOK_LEAD,
    BYOK_TITLE,
    CORTEX_KEY_LABEL,
    FORBIDDEN_SUBSCRIBE_TERMS,
    FREE_LEAD,
    FREE_STEPS,
    FREE_TITLE,
    SUBSCRIBE_BUTTON,
    SUBSCRIBE_DISCLOSURE,
    SUBSCRIBE_EMPTY_HINT,
    SUBSCRIBE_ISSUED_HINT,
    SUBSCRIBE_LEAD,
    SUBSCRIBE_TITLE,
)
from openmw.openvault.vault.store import KeyVault


def build_key_ui_router(vault: KeyVault, accounts: AccountStore) -> APIRouter:
    router = APIRouter(tags=["key-ui"])

    @router.get("/api/keys/ui-copy")
    def keys_ui_copy() -> dict[str, Any]:
        return {
            "subscribe": {
                "title": SUBSCRIBE_TITLE,
                "lead": SUBSCRIBE_LEAD,
                "button": SUBSCRIBE_BUTTON,
                "issued_label": CORTEX_KEY_LABEL,
                "issued_hint": SUBSCRIBE_ISSUED_HINT,
                "empty_hint": SUBSCRIBE_EMPTY_HINT,
                "disclosure": SUBSCRIBE_DISCLOSURE,
            },
            "byok": {"title": BYOK_TITLE, "lead": BYOK_LEAD},
            "free": {
                "title": FREE_TITLE,
                "lead": FREE_LEAD,
                "steps": [{"title": title, "body": body} for title, body in FREE_STEPS],
            },
            "forbidden_subscribe_terms": list(FORBIDDEN_SUBSCRIBE_TERMS),
        }

    @router.post("/api/keys/cortex")
    def issue_operator_cortex_key() -> dict[str, Any]:
        record, token = issue_cortex_key(vault, account_id=None)
        return issued_payload(record, token)

    @router.post("/api/accounts/{account_id}/cortex-key")
    def issue_account_cortex_key(account_id: str) -> dict[str, Any]:
        if accounts.get(account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
        record, token = issue_cortex_key(vault, account_id=account_id)
        return issued_payload(record, token)

    return router
