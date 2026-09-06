"""Loopback app grants -- another local app asks, the human approves, one ov_ token.

Not SaaS embed (F13). Not passkeys. The other app must be on this machine.
The token is delivered once on poll and is never written to disk.

The pairing code is the process boundary loopback does not give us (KB A-0009).
``docs/SECRETS_CUSTODY.md`` is blunt about it: any process running as the user
reaches 127.0.0.1, so "came from this machine" plus a header the caller writes
about itself identifies nobody. The code is minted here, returned **once** to
the app that asked, and read back by the human from that app's own screen. So
the approval binds to the process holding the code, not to whoever POSTs first.
That only holds while the code stays out of every other response: ``to_public``
withholds it unless asked, and only ``start_grant`` asks.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openmw.openvault.paths import ensure_home

TTL_S = 900
#: A 4-hex-char code is 65536 guesses, and loopback has no rate limit of its
#: own. Burn the grant after this many wrong ones so the window cannot be walked.
MAX_CODE_ATTEMPTS = 5
IssueFn = Callable[[str], tuple[str, str]]

#: Tokens wait here until one poll. Never written to app_grants.json.
_READY_TOKENS: dict[str, str] = {}


class GrantRefusedError(PermissionError):
    """The pairing code was missing or wrong. A refusal, not a bad request."""


@dataclass
class AppGrant:
    grant_id: str
    client_name: str
    label: str
    user_code: str
    status: str
    created_at: float
    expires_at: float
    api_key_id: str = ""
    attempts: int = 0

    def to_public(self, *, include_code: bool = False) -> dict[str, Any]:
        """Public view. ``include_code`` is for the asking app's own reply only.

        Any other caller that could read ``user_code`` back off the API could
        also replay it into a decision, which puts us back where A-0009 found us.
        """
        web = int(os.environ.get("OPENVAULT_WEB_PORT") or 3010)
        row = {
            "grant_id": self.grant_id,
            "client_name": self.client_name,
            "label": self.label,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "approve_url": f"http://127.0.0.1:{web}/grant/{self.grant_id}",
            "protocol_url": f"openvault://grant/{self.grant_id}",
        }
        if include_code:
            row["user_code"] = self.user_code
        return row


def _store_path() -> Path:
    return ensure_home() / "app_grants.json"


def _load() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {"grants": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"grants": {}}
    grants = raw.get("grants") if isinstance(raw, dict) else None
    if not isinstance(grants, dict):
        return {"grants": {}}
    return {"grants": grants}


def _save(state: dict[str, Any]) -> None:
    _store_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def _row(raw: dict[str, Any]) -> AppGrant:
    return AppGrant(
        grant_id=str(raw.get("grant_id") or ""),
        client_name=str(raw.get("client_name") or ""),
        label=str(raw.get("label") or ""),
        user_code=str(raw.get("user_code") or ""),
        status=str(raw.get("status") or "pending"),
        created_at=float(raw.get("created_at") or 0.0),
        expires_at=float(raw.get("expires_at") or 0.0),
        api_key_id=str(raw.get("api_key_id") or ""),
        attempts=int(raw.get("attempts") or 0),
    )


def _put(grant: AppGrant) -> None:
    state = _load()
    state["grants"][grant.grant_id] = asdict(grant)
    _save(state)


def _get(grant_id: str) -> AppGrant | None:
    key = (grant_id or "").strip()
    if not key:
        return None
    raw = _load()["grants"].get(key)
    if not isinstance(raw, dict):
        return None
    grant = _row(raw)
    if grant.status == "pending" and grant.expires_at < time.time():
        grant.status = "expired"
        _put(grant)
    return grant


def start_grant(*, client_name: str, label: str = "") -> dict[str, Any]:
    name = (client_name or "").strip()[:80]
    if not name:
        raise ValueError("client_name is required")
    now = time.time()
    grant = AppGrant(
        grant_id=uuid.uuid4().hex[:12],
        client_name=name,
        label=(label or name).strip()[:80] or name,
        user_code=secrets.token_hex(2).upper(),
        status="pending",
        created_at=now,
        expires_at=now + TTL_S,
    )
    _put(grant)
    # The only response that carries the code. The asking app shows it; the
    # human reads it off that screen and types it into OpenVault.
    return grant.to_public(include_code=True)


def public_grant(grant_id: str) -> dict[str, Any] | None:
    grant = _get(grant_id)
    if grant is None:
        return None
    return grant.to_public()


def list_pending() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in _load()["grants"].values():
        if not isinstance(raw, dict):
            continue
        grant = _row(raw)
        if grant.status == "pending" and grant.expires_at >= time.time():
            out.append(grant.to_public())
    return out


def _code_matches(supplied: str, expected: str) -> bool:
    """Constant-time compare of the typed code against the minted one.

    ``==`` on a secret short-circuits at the first wrong character, so the
    timing of a rejected guess reports how much of the code was right and 65536
    guesses collapse to about 64. Case and whitespace are normalised first --
    that is on the value the caller typed, so it leaks nothing about ours -- and
    the compare runs on bytes, because ``compare_digest`` raises on non-ASCII
    ``str`` and a caller can send any code point it likes.
    """
    left = (supplied or "").strip().upper()
    right = (expected or "").strip().upper()
    if not left or not right:
        return False
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def decide_grant(
    grant_id: str,
    *,
    approve: bool,
    user_code: str,
    issue: IssueFn,
) -> dict[str, Any]:
    """Approve or deny a pending grant. Requires the code the asking app holds.

    The code gates deny as well as approve: a wrong-code deny is another local
    process cancelling a grant it cannot see, which is a smaller theft but the
    same missing boundary.
    """
    grant = _get(grant_id)
    if grant is None:
        raise KeyError("grant not found")
    if grant.status != "pending":
        raise ValueError(f"grant is {grant.status}")
    if grant.expires_at < time.time():
        grant.status = "expired"
        _put(grant)
        raise ValueError("grant expired")
    if not _code_matches(user_code, grant.user_code):
        grant.attempts += 1
        if grant.attempts >= MAX_CODE_ATTEMPTS:
            # Burn it rather than leave a guessable grant open for the TTL. The
            # asking app sees denied on its next poll and can start a new one.
            grant.status = "denied"
        _put(grant)
        raise GrantRefusedError("pairing code does not match")
    if not approve:
        grant.status = "denied"
        _put(grant)
        return grant.to_public()
    key_id, token = issue(grant.label)
    grant.status = "ready"
    grant.api_key_id = key_id
    _READY_TOKENS[grant.grant_id] = token
    _put(grant)
    return grant.to_public()


def poll_grant(grant_id: str) -> dict[str, Any]:
    """Return the token at most once. Later polls say delivered, never the secret."""
    grant = _get(grant_id)
    if grant is None:
        return {"status": "missing", "token": None}
    if grant.status == "pending":
        return {"status": "pending", "token": None}
    if grant.status in ("denied", "expired", "delivered"):
        return {"status": grant.status, "token": None}
    token = _READY_TOKENS.pop(grant.grant_id, "")
    grant.status = "delivered"
    _put(grant)
    return {
        "status": "ready",
        "token": token or None,
        "api_key_id": grant.api_key_id,
        "warning": "This is the only time the token is shown.",
    }


__all__ = [
    "MAX_CODE_ATTEMPTS",
    "GrantRefusedError",
    "decide_grant",
    "list_pending",
    "poll_grant",
    "public_grant",
    "start_grant",
]
