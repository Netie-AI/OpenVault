"""Shared test helpers.

``issue_key`` exists because FreeRoute stopped believing headers. Tests used to
select a metered tier with ``x-openfree-tier: free`` and invent a caller with
``x-openfree-identity``; both are now ignored, because any real caller could do
the same thing and buy themselves the unmetered ``local`` tier. A test that
wants to be metered now holds a credential, exactly like the third-party app it
is standing in for.
"""

from __future__ import annotations

from typing import Any


def issue_key(
    client: Any, *, tier: str = "free", label: str = "test-suite"
) -> tuple[str, dict[str, str]]:
    """Mint a real API key. Returns (identity, auth headers).

    The caller identity IS the key id — that is what the rate limiter buckets on
    and what the usage ledger attributes spend to.
    """
    resp = client.post("/api/apikeys", json={"label": label, "tier": tier})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    return out["key"]["key_id"], {"Authorization": f"Bearer {out['token']}"}
