"""Issue an ov_ token and frame it as a Cortex API key.

Tiny write path for the subscribe screen. Tenant BYOK stays on
``POST /api/accounts/{id}/keys`` and is not a pooled spend path.
"""

from __future__ import annotations

import secrets
from dataclasses import asdict
from typing import Any

from openmw.openvault.vault.key_ui_copy import CORTEX_KEY_LABEL
from openmw.openvault.vault.store import KeyRecord, KeyVault

TOKEN_PREFIX = "ov_"


def mint_cortex_token() -> str:
    """Return a new ov_ token. Shown once; vault stores the ciphertext."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def issue_cortex_key(
    vault: KeyVault,
    *,
    account_id: str | None = None,
    label: str = CORTEX_KEY_LABEL,
) -> tuple[KeyRecord, str]:
    """Store a Cortex-framed ov_ token. Never marks the row as pooled spend."""
    token = mint_cortex_token()
    record = vault.create(
        label=label,
        provider="cortex",
        secret=token,
        role="primary",
        priority=10,
        account_id=account_id,
    )
    return record, token


def issued_payload(record: KeyRecord, token: str) -> dict[str, Any]:
    body = asdict(record)
    body["display_label"] = CORTEX_KEY_LABEL
    body["token"] = token
    body["custody"] = "tenant" if record.account_id else "operator"
    body["pooled"] = False
    return body
