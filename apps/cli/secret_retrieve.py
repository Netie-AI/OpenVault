"""Agent thin-client retrieve for OpenVault secrets (issue #39).

Stdlib only: the launcher runs on system Python and must not import OpenMW.
Calls live HTTP reveal on loopback. Hard-denies payment_card / PAN. Never
writes retrieved passwords to disk.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

REVEAL_HEADER = "X-OpenVault-Reveal"
REVEAL_VALUE = "intentional"
ADMIN_HEADER = "X-OpenVault-Admin"
PAN_DENY = "payment_card / PAN is denied to agents; OpenVault will not return card numbers"
AGENT_SECRET_KINDS = frozenset({"password", "totp"})
_SITE_TARGET_RE = re.compile(r"(PASSWORD|PASSWD)|(^SITE_)", re.I)

HttpFn = Callable[[str, str, dict[str, str] | None, bytes | None], tuple[int, dict[str, Any], str]]


class RetrieveError(RuntimeError):
    """Closed failure: sealed, denied, missing, or transport."""

    def __init__(self, message: str, *, status: int = 1) -> None:
        super().__init__(message)
        self.status = status


def admin_token_path() -> Path:
    """Same 0600 file the server writes. Never an argv value."""
    override = (os.environ.get("OPENVAULT_ADMIN_TOKEN_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    home = (os.environ.get("OPENVAULT_HOME") or "").strip()
    root = Path(home).expanduser() if home else Path.home() / ".openvault"
    return root / "admin_token"


def read_admin_token() -> str:
    path = admin_token_path()
    if not path.is_file():
        raise RetrieveError(f"admin token file is missing: {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise RetrieveError(f"admin token file is empty: {path}")
    return token


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


def _looks_like_site_login_target(wanted: str) -> bool:
    """SITE_* / *PASSWORD labels are secrets, never /api/keys hops."""
    return bool(_SITE_TARGET_RE.search((wanted or "").strip()))


def _admin_call(
    transport: HttpFn, root: str
) -> Callable[[str, str, dict[str, str] | None], tuple[int, dict[str, Any], str]]:
    """Bind the transport to the loopback root and the admin header every route here needs."""

    def call(
        method: str, path: str, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, Any], str]:
        merged = dict(headers or {})
        merged[ADMIN_HEADER] = read_admin_token()
        return transport(method, f"{root}{path}", merged, None)

    return call


def _sweep_site_password_hops(
    call: Callable[[str, str, dict[str, str] | None], tuple[int, dict[str, Any], str]],
) -> None:
    """Move leftover SITE_/password hops into SecretStore. Ignore if sealed/old."""
    call("POST", "/api/vault/migrate-site-passwords", None)


def _normalize_agent_kind(kind_hint: str | None) -> str:
    wanted = (kind_hint or "").strip().lower()
    if wanted in {"site", "passwords"}:
        return "password"
    if wanted in {"otp", "otpauth"}:
        return "totp"
    return wanted


def _row_search_blob(row: dict[str, Any]) -> str:
    return " ".join(
        [
            str(row.get("label") or ""),
            str(row.get("username") or ""),
            str(row.get("url") or ""),
        ]
    ).casefold()


def list_secrets(
    base_url: str,
    *,
    kind_hint: str | None = None,
    query: str | None = None,
    http: HttpFn | None = None,
) -> dict[str, Any]:
    """Masked site logins and TOTP rows. Never cards. Never decrypts."""
    if _is_card_kind(kind_hint):
        raise RetrieveError(PAN_DENY)

    transport = http or (lambda method, url, headers, body: urllib_http(method, url, headers, body))
    call = _admin_call(transport, base_url.rstrip("/"))
    status, payload, text = call("GET", "/api/vault/status")
    if status != 200:
        raise RetrieveError(_detail(payload, text, "vault status failed"), status=status)
    if payload.get("sealed") is True:
        raise RetrieveError("vault is sealed; POST /api/vault/unseal with the passphrase first")

    _sweep_site_password_hops(call)

    status, payload, text = call("GET", "/api/secrets")
    if status != 200:
        raise RetrieveError(_detail(payload, text, "list secrets failed"), status=status)

    wanted = _normalize_agent_kind(kind_hint)
    if wanted and wanted not in AGENT_SECRET_KINDS:
        raise RetrieveError(f"agents list {sorted(AGENT_SECRET_KINDS)}, not {wanted}")
    needle = (query or "").strip().casefold()

    rows: list[dict[str, Any]] = []
    for row in payload.get("secrets") or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        if _is_card_kind(kind):
            continue
        if kind not in AGENT_SECRET_KINDS:
            continue
        if wanted and kind != wanted:
            continue
        if needle and needle not in _row_search_blob(row) and needle not in str(row.get("id") or "").casefold():
            continue
        rows.append(
            {
                "id": row.get("id"),
                "kind": kind,
                "label": row.get("label"),
                "masked": row.get("masked"),
                "username": row.get("username") or "",
                "url": row.get("url") or "",
                "lifecycle": row.get("lifecycle") or "active",
            }
        )
    return {"secrets": rows, "count": len(rows)}


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
    call = _admin_call(transport, base_url.rstrip("/"))

    status, payload, text = call("GET", "/api/vault/status")
    if status != 200:
        raise RetrieveError(_detail(payload, text, "vault status failed"), status=status)
    if payload.get("sealed") is True:
        raise RetrieveError("vault is sealed; POST /api/vault/unseal with the passphrase first")

    _sweep_site_password_hops(call)

    wanted = target.strip()
    if not wanted:
        raise RetrieveError("secret id or label is required")

    kind = _normalize_agent_kind(kind_hint)
    if kind == "password":
        return _reveal_password(call, wanted)
    if kind == "totp":
        return _current_totp(call, wanted)
    if kind in {"key", "api_key", "keys"}:
        if _looks_like_site_login_target(wanted):
            raise RetrieveError("site passwords are /api/secrets, not /api/keys")
        return _reveal_key(call, wanted)
    # Auto: passwords/TOTP first (explicit deny if the id is a card), then keys.
    password = _find_secret_row(call, wanted)
    if password is not None:
        if _is_card_kind(str(password.get("kind"))):
            raise RetrieveError(PAN_DENY)
        if str(password.get("kind") or "") == "totp":
            return _current_totp_row(call, password)
        return _reveal_password_row(call, password)
    if _looks_like_site_login_target(wanted):
        raise RetrieveError(
            f"site login not in secrets: {wanted}. Use `openvault secret list --kind password`.",
            status=404,
        )
    return _reveal_key(call, wanted)


def _find_secret_row(
    call: Callable[[str, str, dict[str, str] | None], tuple[int, dict[str, Any], str]],
    wanted: str,
    *,
    kind: str | None = None,
) -> dict[str, Any] | None:
    status, payload, text = call("GET", "/api/secrets", None)
    if status != 200:
        raise RetrieveError(_detail(payload, text, "list secrets failed"), status=status)
    rows = [row for row in (payload.get("secrets") or []) if isinstance(row, dict)]
    return _pick_secret_row(rows, wanted, kind=kind)


def _pick_secret_row(
    rows: list[dict[str, Any]],
    wanted: str,
    *,
    kind: str | None = None,
) -> dict[str, Any] | None:
    """Exact id/label wins. A unique label/url/username substring is enough.

    Several matches is a closed failure so an agent cannot grab the wrong login.
    """
    needle = wanted.strip()
    if not needle:
        return None
    folded = needle.casefold()
    kind_want = _normalize_agent_kind(kind) if kind else ""
    if kind_want and kind_want not in AGENT_SECRET_KINDS:
        kind_want = ""

    def _kind_ok(row: dict[str, Any]) -> bool:
        row_kind = str(row.get("kind") or "")
        if kind_want == "password":
            return row_kind == "password"
        if kind_want == "totp":
            return row_kind == "totp"
        return True

    exact: list[dict[str, Any]] = []
    for row in rows:
        if not _kind_ok(row):
            continue
        if row.get("id") == wanted or (row.get("label") or "") == wanted:
            exact.append(row)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        labels = ", ".join(str(r.get("label") or r.get("id")) for r in exact[:8])
        raise RetrieveError(f"ambiguous secret match: {labels}")

    loose: list[dict[str, Any]] = []
    for row in rows:
        if not _kind_ok(row):
            continue
        row_kind = str(row.get("kind") or "")
        if _is_card_kind(row_kind) or row_kind not in AGENT_SECRET_KINDS:
            continue
        if folded in _row_search_blob(row):
            loose.append(row)
    if len(loose) == 1:
        return loose[0]
    if len(loose) > 1:
        labels = ", ".join(str(r.get("label") or r.get("id")) for r in loose[:8])
        raise RetrieveError(
            f"ambiguous secret match: {labels}. Use the exact label from `openvault secret list`."
        )
    return None


def _reveal_password(
    call: Callable[[str, str, dict[str, str] | None], tuple[int, dict[str, Any], str]],
    wanted: str,
) -> dict[str, Any]:
    row = _find_secret_row(call, wanted, kind="password")
    if row is None:
        raise RetrieveError(f"password not found: {wanted}", status=404)
    if _is_card_kind(str(row.get("kind"))):
        raise RetrieveError(PAN_DENY)
    return _reveal_password_row(call, row)


def _current_totp(
    call: Callable[[str, str, dict[str, str] | None], tuple[int, dict[str, Any], str]],
    wanted: str,
) -> dict[str, Any]:
    row = _find_secret_row(call, wanted, kind="totp")
    if row is None:
        raise RetrieveError(f"totp not found: {wanted}", status=404)
    if _is_card_kind(str(row.get("kind"))):
        raise RetrieveError(PAN_DENY)
    return _current_totp_row(call, row)


def _current_totp_row(
    call: Callable[[str, str, dict[str, str] | None], tuple[int, dict[str, Any], str]],
    row: dict[str, Any],
) -> dict[str, Any]:
    secret_id = str(row.get("id") or "")
    headers = {REVEAL_HEADER: REVEAL_VALUE}
    status, payload, text = call("GET", f"/api/secrets/{secret_id}/current-code", headers)
    if status != 200:
        raise RetrieveError(_detail(payload, text, "totp current-code failed"), status=status)
    code = payload.get("code")
    return {
        "kind": "totp",
        "id": secret_id,
        "label": row.get("label"),
        "code": code,
        "digits": payload.get("digits") or (len(str(code)) if code else None),
        "cached": False,
    }


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
