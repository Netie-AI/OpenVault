"""Friendly key UI routes -- subscribe Cortex issue + copy. No hop spend.

Mounted by the integrator with ``build_key_ui_router(vault, accounts, ...)``.
Minting a Cortex key writes a row into the vault, so it is a custody mutation
like ``POST /api/keys``. The app passes its own controls in through ``guard``
(loopback + unsealed) and ``audit`` (the custody audit line) rather than this
module re-implementing them -- a second "is this loopback" is a second thing
to get wrong. Both default to no-op so the router can be mounted on a bare
FastAPI app in tests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

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
from openmw.openvault.vault.store import KeyRecord, KeyVault

#: Runs before a mint; raise ``HTTPException`` to refuse. Receives the action name.
GuardHook = Callable[[Request, str], None]
#: Runs after a mint with the stored record (never the plaintext token).
AuditHook = Callable[[Request, KeyRecord], None]

_ISSUE_ACTION = "cortex key issue"


def build_key_ui_router(
    vault: KeyVault,
    accounts: AccountStore,
    *,
    guard: GuardHook | None = None,
    audit: AuditHook | None = None,
) -> APIRouter:
    router = APIRouter(tags=["key-ui"])

    def _issue(request: Request, account_id: str | None) -> dict[str, Any]:
        if guard is not None:
            guard(request, _ISSUE_ACTION)
        if account_id is not None and accounts.get(account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
        record, token = issue_cortex_key(vault, account_id=account_id)
        if audit is not None:
            audit(request, record)
        return issued_payload(record, token)

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
    def issue_operator_cortex_key(request: Request) -> dict[str, Any]:
        return _issue(request, None)

    @router.post("/api/accounts/{account_id}/cortex-key")
    def issue_account_cortex_key(account_id: str, request: Request) -> dict[str, Any]:
        return _issue(request, account_id)

    return router
