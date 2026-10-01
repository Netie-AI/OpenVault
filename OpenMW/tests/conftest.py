"""Shared test helpers.

``issue_key`` exists because FreeRoute stopped believing headers. Tests used to
select a metered tier with ``x-openfree-tier: free`` and invent a caller with
``x-openfree-identity``; both are now ignored, because any real caller could do
the same thing and buy themselves the unmetered ``local`` tier. A test that
wants to be metered now holds a credential, exactly like the third-party app it
is standing in for.
"""

from __future__ import annotations

import contextvars
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from openmw.openvault.vault.admin_token import (
    ADMIN_HEADER,
    admin_headers,
    ensure_admin_token,
    path_needs_admin,
)

#: Tests send the real admin token on key/secret routes. Set false to prove a
#: keyless call. This does not turn the gate off inside the app.
inject_admin_credential: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "openvault_test_inject_admin",
    default=True,
)

_ORIGINAL_REQUEST = TestClient.request


def _request_path(url: object) -> str:
    raw = str(url)
    if "://" in raw:
        raw = urlsplit(raw).path
    return raw.split("?", 1)[0]


def _has_admin_header(headers: object) -> bool:
    if not headers:
        return False
    if isinstance(headers, Mapping):
        pairs = headers.items()
    else:
        try:
            pairs = list(headers)  # type: ignore[arg-type]
        except TypeError:
            return False
    return any(str(key).lower() == ADMIN_HEADER.lower() for key, _value in pairs)


def _with_admin_header(headers: object) -> dict[str, str]:
    merged: dict[str, str] = {}
    if isinstance(headers, Mapping):
        merged = {str(key): str(value) for key, value in headers.items()}
    elif headers:
        merged = {str(key): str(value) for key, value in headers}  # type: ignore[misc]
    merged.update(admin_headers())
    return merged


def _request_with_admin(self: TestClient, method: str, url: object, **kwargs: Any) -> Any:
    needs = path_needs_admin(_request_path(url))
    missing = not _has_admin_header(kwargs.get("headers"))
    if inject_admin_credential.get() and needs and missing:
        kwargs["headers"] = _with_admin_header(kwargs.get("headers"))
    return _ORIGINAL_REQUEST(self, method, url, **kwargs)


@pytest.fixture(autouse=True)
def _send_admin_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing admin-route callers present the 0600 token. The gate still runs."""
    monkeypatch.setattr(TestClient, "request", _request_with_admin)


def issue_key(
    client: Any, *, tier: str = "free", label: str = "test-suite"
) -> tuple[str, dict[str, str]]:
    """Mint a real API key. Returns (identity, auth headers).

    The caller identity IS the key id — that is what the rate limiter buckets on
    and what the usage ledger attributes spend to. Minting is an admin route, so
    the admin token is sent from the 0600 file, never from argv.
    """
    resp = client.post(
        "/api/apikeys",
        json={"label": label, "tier": tier},
        headers=admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    return out["key"]["key_id"], {"Authorization": f"Bearer {out['token']}"}


def read_admin_token() -> str:
    """The token the suite's admin calls present."""
    return ensure_admin_token()
