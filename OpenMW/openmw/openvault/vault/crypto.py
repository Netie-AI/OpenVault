"""Local sealing for OpenVault secrets (Fernet + machine master key).

The master key is stored wrapped — see ``vault/keywrap.py``. On Windows that
means DPAPI user scope, so copying ``master.key`` + ``keys.db`` to another
machine or another account no longer recovers the secrets. Legacy plaintext
keys are migrated on first open.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import structlog
from cryptography.fernet import Fernet, InvalidToken

from openmw.openvault.paths import master_key_path
from openmw.openvault.vault import keywrap

log = structlog.get_logger()


class VaultCryptoError(RuntimeError):
    """Raised when ciphertext cannot be decrypted."""


def _write_key_file(key_path: Path, key: bytes) -> str:
    """Write the wrapped key and verify it reads back before trusting it.

    The read-back is not paranoia: if wrapping succeeds but unwrapping fails
    (wrong entropy, a DPAPI edge case), we would have replaced a working key
    file with an unreadable one and made every stored secret unrecoverable.
    Verifying before the caller proceeds keeps that failure recoverable.
    """
    blob = keywrap.wrap(key)
    round_tripped, method = keywrap.unwrap(blob)
    if round_tripped != key:
        raise VaultCryptoError(
            "master key failed to round-trip through the platform key wrapper; "
            "refusing to write a key file that cannot be read back"
        )
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(blob)
    with contextlib.suppress(OSError):
        key_path.chmod(0o600)
    return method


def _migrate_plaintext_key(key_path: Path, key: bytes) -> None:
    """Wrap a legacy v0 raw key in place, keeping a backup.

    Best-effort: a machine where wrapping is unavailable keeps working exactly
    as before rather than losing access to its own vault. The backup stays
    until the user removes it — an unreadable vault is a far worse outcome
    than a leftover file, and it is the only recovery path if the wrapped key
    later fails to unwrap.
    """
    backup = key_path.with_suffix(key_path.suffix + ".v0.bak")
    try:
        if not backup.exists():
            backup.write_bytes(key_path.read_bytes())
            with contextlib.suppress(OSError):
                backup.chmod(0o600)
        method = _write_key_file(key_path, key)
    except (keywrap.KeyWrapError, VaultCryptoError, OSError) as exc:
        log.warning("master_key_migration_skipped", error=str(exc))
        return
    log.info("master_key_wrapped", method=method, backup=str(backup))


def _load_or_create_master_key(path: Path | None = None) -> bytes:
    key_path = path if path is not None else master_key_path()

    if key_path.is_file():
        blob = key_path.read_bytes()
        if keywrap.is_wrapped(blob):
            try:
                key, _method = keywrap.unwrap(blob)
            except keywrap.KeyWrapError as exc:
                # Every stored secret is unreadable from here. Say why, and
                # point at the backup, rather than surfacing a bare stack trace.
                raise VaultCryptoError(
                    f"cannot unwrap the vault master key: {exc}. If this vault "
                    f"was created by a different Windows account, sign in as "
                    f"that account. A pre-migration copy may exist at "
                    f"{key_path.with_suffix(key_path.suffix + '.v0.bak')}"
                ) from exc
            return key

        # Legacy v0: a raw Fernet key in plaintext. Use it, then upgrade it.
        key = blob.strip()
        _migrate_plaintext_key(key_path, key)
        return key

    key = Fernet.generate_key()
    _write_key_file(key_path, key)
    return key


class Seal:
    """Encrypt/decrypt vault payloads with a local Fernet master key."""

    def __init__(self, master_key: bytes | None = None, *, key_path: Path | None = None) -> None:
        raw = master_key if master_key is not None else _load_or_create_master_key(key_path)
        self._fernet = Fernet(raw)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise VaultCryptoError("unable to decrypt vault secret") from exc


def mask_secret(secret: str, *, visible: int = 4) -> str:
    """Return a masked preview suitable for API responses and logs."""
    if not secret:
        return ""
    if len(secret) <= visible * 2:
        return "*" * len(secret)
    return f"{secret[:visible]}…{'*' * min(8, len(secret) - visible)}"
