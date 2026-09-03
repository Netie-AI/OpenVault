"""Who is calling FreeRoute — resolved from a credential, not from a header.

The gateway used to take the caller's word for it::

    identity = request.headers.get("x-openfree-identity") or client_host
    tier     = request.headers.get("x-openfree-tier") or DEFAULT_TIER

``DEFAULT_TIER`` is ``local``: 6000 requests and 6,000,000 tokens a minute. So
one header bought an effectively unmetered pool of *our* vaulted provider keys,
and a caller who did hit a limit could change the identity string and get a
fresh bucket. Both headers are now ignored entirely.

What replaces them has to keep local development working, or the control is a
failure of a different kind (R-0005 - a control that refuses legitimate work).
So:

* An ``Authorization: Bearer ov_...`` is verified against the issued-key store.
  Unknown or revoked is 401, and the two are indistinguishable to the caller.
* No credential from **loopback** keeps the historical developer tier. The
  socket peer address decides this, never a forwarded header, so it cannot be
  spoofed by a proxy hop.
* No credential from **anywhere else** is 401. A remote caller is exactly the
  third-party case that has to present a key.
* ``OPENVAULT_REQUIRE_API_KEY=1`` drops the loopback exemption too - the
  posture to run in when the gateway is actually exposed.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

log = structlog.get_logger()

#: Loopback keeps the historical unmetered developer tier.
LOOPBACK_TIER = "local"
_BEARER = "bearer "


class _KeyStore(Protocol):
    def verify(self, token: str) -> Any: ...

    def touch(self, key_id: str) -> None: ...


@dataclass(frozen=True)
class Caller:
    """The authenticated subject of one gateway request."""

    identity: str
    tier: str
    #: Set only when an issued key was presented. None for loopback dev traffic.
    api_key_id: str | None = None
    #: "api_key" | "loopback"
    source: str = "loopback"
    label: str = ""

    @property
    def metered(self) -> bool:
        return self.api_key_id is not None


class AuthRefusedError(Exception):
    """401 with a typed body — raised instead of returning a fake identity."""

    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = {
            "error": {
                "message": message,
                "type": "openvault_unauthenticated",
            }
        }


def require_api_key() -> bool:
    return (os.environ.get("OPENVAULT_REQUIRE_API_KEY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def is_loopback(host: str) -> bool:
    """True only for a real loopback socket peer. Never reads a header.

    Deliberately the same notion of "this machine" as the custody gate's
    ``_normalise_host`` in app.py — including the IPv4-mapped IPv6 form a
    dual-stack socket reports. Two different definitions of loopback in one
    process is how one of them ends up wrong.

    Starlette's in-process ``TestClient`` reports the peer as ``"testclient"``
    and is NOT accepted: tests that need to look local pass
    ``TestClient(app, client=("127.0.0.1", 5555))``, which is already the
    convention every custody test in this repo follows.
    """
    value = (host or "").strip()
    if not value:
        return False
    if value.startswith("::ffff:"):
        value = value[len("::ffff:") :]
    if value in ("localhost", "local"):
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def bearer_token(request: Any) -> str:
    raw = request.headers.get("authorization") or ""
    if raw.lower().startswith(_BEARER):
        return raw[len(_BEARER) :].strip()
    # Some SDKs send the key in this header instead; accept it rather than
    # bouncing a caller who is holding a valid credential.
    return (request.headers.get("x-api-key") or "").strip()


def resolve_caller(request: Any, *, api_keys: _KeyStore) -> Caller:
    """Authenticate one gateway request. Raises :class:`AuthRefusedError` on 401."""
    client_host = request.client.host if getattr(request, "client", None) is not None else ""
    token = bearer_token(request)

    if token:
        record = api_keys.verify(token)
        if record is None:
            log.info("freeroute_auth_rejected", reason="unknown_or_revoked_key")
            raise AuthRefusedError(
                "that API key is not valid. Issue one with POST /api/apikeys, or "
                "check it has not been revoked."
            )
        try:
            api_keys.touch(record.key_id)
        except Exception as exc:  # pragma: no cover - last-used is not load-bearing
            log.warning("api_key_touch_failed", error=str(exc))
        return Caller(
            identity=record.key_id,
            tier=record.tier,
            api_key_id=record.key_id,
            source="api_key",
            label=record.label,
        )

    if require_api_key():
        raise AuthRefusedError(
            "this OpenVault requires an API key: send Authorization: Bearer ov_..."
        )

    if is_loopback(client_host):
        return Caller(identity=client_host or "local", tier=LOOPBACK_TIER, source="loopback")

    log.info("freeroute_auth_rejected", reason="remote_without_key", client=client_host)
    raise AuthRefusedError(
        "remote callers must present an API key: Authorization: Bearer ov_... "
        "(issue one with POST /api/apikeys)"
    )


__all__ = [
    "LOOPBACK_TIER",
    "AuthRefusedError",
    "Caller",
    "bearer_token",
    "is_loopback",
    "require_api_key",
    "resolve_caller",
]
