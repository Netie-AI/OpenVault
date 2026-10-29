"""Local admin credential for key and secret management routes.

This is not an issued ``ov_`` API key. It lives in a mode-0600 file under the
vault data dir and is sent as ``X-OpenVault-Admin``. Callers compare it with
``hmac.compare_digest``. The value is never logged.

Override the file location with ``OPENVAULT_ADMIN_TOKEN_PATH``. Otherwise the
file is ``<OPENVAULT_HOME>/admin_token`` (default ``~/.openvault/admin_token``).
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
from pathlib import Path

import structlog

from openmw.openvault.paths import ensure_home, openvault_home

log = structlog.get_logger()
_py_log = logging.getLogger(__name__)

ADMIN_HEADER = "X-OpenVault-Admin"
ADMIN_TOKEN_FILENAME = "admin_token"
ADMIN_TOKEN_PATH_ENV = "OPENVAULT_ADMIN_TOKEN_PATH"

_ADMIN_ROOTS: tuple[str, ...] = (
    "/api/keys",
    "/api/keyvault",
    "/api/apikeys",
    "/api/secrets",
    "/api/vault",
    "/api/ship/github/pat",
    "/keys",
)
_ACCOUNT_KEY = re.compile(r"^/api/accounts/[^/]+/(?:keys|cortex-key)(?:/|$)")


def admin_token_path() -> Path:
    """Resolved token file. An env override replaces the vault-home default."""
    override = (os.environ.get(ADMIN_TOKEN_PATH_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return openvault_home() / ADMIN_TOKEN_FILENAME


def path_needs_admin(path: str) -> bool:
    """True for key/secret management routes and ``/keys``.

    ``/api/healthz``, ``/api/freeroute/status``, ``/v1/*``, and
    ``/.well-known/jwks.json`` are not admin routes.

    This is path classification only. The HTTP guard skips the admin header
    on three POST signing-mint routes: exact ``/keys/services``, exact
    ``/keys/intermediate``, and ``/keys/intermediate/{kid}/revoke``. GET and
    every other method on those paths stay admin routes.

    ``/api/apikeys/verify`` stays classified here so GET and every near path
    still require the admin header. ``refuse_if_unauthorised`` admits an
    allowlisted service Bearer on exact ``POST /api/apikeys/verify`` only.

    ``/api/keys/leases`` and ``/api/keys/leases/redeem`` stay classified here
    so GET and every other method still require the admin header.
    ``refuse_if_unauthorised`` admits a service Bearer on those two exact
    POSTs only. The admin header does not admit them.
    """
    for root in _ADMIN_ROOTS:
        if path == root or path.startswith(root + "/"):
            return True
    return _ACCOUNT_KEY.match(path) is not None


def ensure_admin_token() -> str:
    """Return the admin token, creating a mode-0600 file on first use."""
    path = admin_token_path()
    if path.is_file():
        existing = _read_token(path)
        if existing:
            _restrict(path)
            return existing
    if (os.environ.get(ADMIN_TOKEN_PATH_ENV) or "").strip():
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        ensure_home()
    token = secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        existing = _read_token(path)
        if existing:
            _restrict(path)
            return existing
        raise
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    _restrict(path)
    log.info("openvault_admin_token_ready", created=True)
    _py_log.info("openvault_admin_token_ready created=true")
    return token


def admin_token_matches(presented: str) -> bool:
    """Constant-time match against the on-disk token. Never logs either value."""
    expected = ensure_admin_token()
    ok = hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
    log.info("openvault_admin_auth", accepted=ok)
    _py_log.info("openvault_admin_auth accepted=%s", str(ok).lower())
    return ok


def admin_headers() -> dict[str, str]:
    """Header map for a client that may call admin routes. Value stays in the header."""
    return {ADMIN_HEADER: ensure_admin_token()}


def _read_token(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _restrict(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        return


__all__ = [
    "ADMIN_HEADER",
    "ADMIN_TOKEN_FILENAME",
    "ADMIN_TOKEN_PATH_ENV",
    "admin_headers",
    "admin_token_matches",
    "admin_token_path",
    "ensure_admin_token",
    "path_needs_admin",
]
