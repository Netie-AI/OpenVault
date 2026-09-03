"""Agent thin-client retrieve for OpenVault secrets (issue #39).

Stdlib only: the launcher runs on system Python and must not import OpenMW.
Calls live HTTP reveal on loopback. Hard-denies payment_card / PAN. Never
writes retrieved passwords to disk.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

REVEAL_HEADER = "X-OpenVault-Reveal"
REVEAL_VALUE = "intentional"
PAN_DENY = "payment_card / PAN is denied to agents; OpenVault will not return card numbers"

HttpFn = Callable[[str, str, dict[str, str] | None, bytes | None], tuple[int, dict[str, Any], str]]


class RetrieveError(RuntimeError):
    """Closed failure: sealed, denied, missing, or transport."""

    def __init__(self, message: str, *, status: int = 1) -> None:
        super().__init__(message)
        self.status = status


def urllib_http(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, Any], str]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            status = int(resp.status)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
    payload: dict[str, Any] = {}
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    return status, payload, text


def _detail(payload: dict[str, Any], text: str, fallback: str) -> str:
    detail = payload.get("detail")
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(detail, dict) and detail:
        return str(detail)
    return text.strip() or fallback


def _is_card_kind(kind: str | None) -> bool:
    value = (kind or "").strip().lower()
    return value in {"payment_card", "card", "pan", "payment"}


def retrieve_secret(
    base_url: str,
    target: str,
    *,
    kind_hint: str | None = None,
    http: HttpFn | None = None,
) -> dict[str, Any]:
    """Return one API key or one site password. Never a card. Never caches.

    ``http`` is (method, url, headers, body) -> (status, json, text). Tests
    inject TestClient; the CLI uses urllib against loopback :5000.
    """
    if _is_card_kind(kind_hint):
        raise RetrieveError(PAN_DENY)

    transport = http or (lambda method, url, headers, body: urllib_http(method, url, headers, body))
    root = base_url.rstrip("/")

    def call(
        method: str, path: str, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, Any], str]:
        return transport(method, f"{root}{path}", headers, None)

    status, payload, text = call("GET", "/api/vault/status")
    if status != 200:
        raise RetrieveError(_detail(payload, text, "vault status failed"), status=status)
    if payload.get("sealed") is True:
        raise RetrieveError("vault is sealed; POST /api/vault/unseal with the passphrase first")

    wanted = target.strip()
    if not wanted:
        raise RetrieveError("secret id or label is required")

    kind = (kind_hint or "").strip().lower()
    if kind in {"password", "site", "passwords"}:
        return _reveal_password(call, wanted)
    if kind in {"key", "api_key", "keys"}:
        return _reveal_key(call, wanted)
    # Auto: passwords first (explicit deny if the id is a card), then keys.
    password = _find_secret_row(call, wanted)
    if password is not None:
        if _is_card_kind(str(password.get("kind"))):
            raise RetrieveError(PAN_DENY)
        return _reveal_password_row(call, password)
    return _reveal_key(call, wanted)


def _find_secret_row(
    call: Callable[[str, str, dict[str, str] | None], tuple[int, dict[str, Any], str]],
    wanted: str,
) -> dict[str, Any] | None:
    status, payload, text = call("GET", "/api/secrets", None)
    if status != 200:
        raise RetrieveError(_detail(payload, text, "list secrets failed"), status=status)
    rows = payload.get("secrets") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("id") == wanted or (row.get("label") or "") == wanted:
            return row
    return None


def _reveal_password(
    call: Callable[[str, str, dict[str, str] | None], tuple[int, dict[str, Any], str]],
    wanted: str,
) -> dict[str, Any]:
    row = _find_secret_row(call, wanted)
    if row is None:
        raise RetrieveError(f"password not found: {wanted}", status=404)
    if _is_card_kind(str(row.get("kind"))):
        raise RetrieveError(PAN_DENY)
    return _reveal_password_row(call, row)


def _reveal_password_row(
    call: Callable[[str, str, dict[str, str] | None], tuple[int, dict[str, Any], str]],
    row: dict[str, Any],
) -> dict[str, Any]:
    secret_id = str(row.get("id") or "")
    headers = {REVEAL_HEADER: REVEAL_VALUE}
    status, payload, text = call("GET", f"/api/secrets/{secret_id}/reveal", headers)
    if status != 200:
        raise RetrieveError(_detail(payload, text, "password reveal failed"), status=status)
    if _is_card_kind(str(payload.get("kind") or row.get("kind"))):
        raise RetrieveError(PAN_DENY)
    return {
        "kind": "password",
        "id": secret_id,
        "label": row.get("label"),
        "username": row.get("username"),
        "url": row.get("url"),
        "secret": payload.get("secret"),
        "cached": False,
    }


def _reveal_key(
    call: Callable[[str, str, dict[str, str] | None], tuple[int, dict[str, Any], str]],
    wanted: str,
) -> dict[str, Any]:
    status, payload, text = call("GET", "/api/keys", None)
    if status != 200:
        raise RetrieveError(_detail(payload, text, "list keys failed"), status=status)
    rows = payload.get("keys") or []
    match: dict[str, Any] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("id") == wanted or (row.get("label") or "") == wanted:
            match = row
            break
    if match is None:
        raise RetrieveError(f"key not found: {wanted}", status=404)
    key_id = str(match.get("id") or "")
    headers = {REVEAL_HEADER: REVEAL_VALUE}
    status, payload, text = call("GET", f"/api/keys/{key_id}/secret", headers)
    if status != 200:
        raise RetrieveError(_detail(payload, text, "key reveal failed"), status=status)
    return {
        "kind": "api_key",
        "id": key_id,
        "label": match.get("label"),
        "provider": match.get("provider"),
        "secret": payload.get("secret"),
        "cached": False,
    }
