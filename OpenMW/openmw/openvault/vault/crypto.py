"""Local sealing for OpenVault secrets (Fernet + machine master key).

The master key is stored wrapped — see ``vault/keywrap.py``. On Windows that
means DPAPI user scope by default, so copying ``master.key`` + ``keys.db`` to
another machine or another account no longer recovers the secrets. When an
operator sets a passphrase, the on-disk wrap becomes ``passphrase-scrypt`` and
the process starts **sealed** until an explicit unseal. Legacy plaintext keys
are migrated on first open.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import structlog
from cryptography.fernet import Fernet, InvalidToken

from openmw.openvault.paths import master_key_path
from openmw.openvault.vault import keywrap

log = structlog.get_logger()


def plaintext_backup_path(key_path: Path) -> Path:
    """Path of the v0 raw-key backup written at migrate (DR-0010).

    Named ``master.key.v0.bak`` next to the live wrapped key. This file is a
    recovery artefact only — :func:`_load_or_create_master_key` must never
    read it as a live key (copy-open of ``keys.db`` + bak must fail closed).
    """
    return key_path.with_suffix(key_path.suffix + ".v0.bak")


def plaintext_backup_present(key_path: Path | None = None) -> bool:
    path = key_path if key_path is not None else master_key_path()
    return plaintext_backup_path(path).is_file()


class VaultCryptoError(RuntimeError):
    """Raised when ciphertext cannot be decrypted."""


class VaultSealedError(VaultCryptoError):
    """Raised when the vault is locked / never unsealed after restart."""


def _write_key_file(
    key_path: Path,
    key: bytes,
    *,
    method: str | None = None,
    passphrase: str | None = None,
) -> str:
    """Write the wrapped key and verify it reads back before trusting it.

    The read-back is not paranoia: if wrapping succeeds but unwrapping fails
    (wrong entropy, a DPAPI edge case), we would have replaced a working key
    file with an unreadable one and made every stored secret unrecoverable.
    Verifying before the caller proceeds keeps that failure recoverable.
    """
    blob = keywrap.wrap(key, method=method, passphrase=passphrase)
    round_tripped, used = keywrap.unwrap(blob, passphrase=passphrase)
    if round_tripped != key:
        raise VaultCryptoError(
            "master key failed to round-trip through the platform key wrapper; "
            "refusing to write a key file that cannot be read back"
        )
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(blob)
    with contextlib.suppress(OSError):
        key_path.chmod(0o600)
    return used


def _migrate_plaintext_key(key_path: Path, key: bytes) -> None:
    """Wrap a legacy v0 raw key in place, keeping a backup.

    Best-effort: a machine where wrapping is unavailable keeps working exactly
    as before rather than losing access to its own vault. The backup stays
    until the user removes it — an unreadable vault is a far worse outcome
    than a leftover file, and it is the only recovery path if the wrapped key
    later fails to unwrap.
    """
    backup = plaintext_backup_path(key_path)
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
    """Load (or create) a master key that can be unwrapped without a passphrase.

    Passphrase-wrapped keys must not go through this path — use
    :meth:`Seal.unseal` instead. Calling here on a passphrase file raises
    :class:`VaultSealedError`.
    """
    key_path = path if path is not None else master_key_path()

    if key_path.is_file():
        blob = key_path.read_bytes()
        if keywrap.is_wrapped(blob):
            method = keywrap.peek_method(blob)
            if method == keywrap.METHOD_PASSPHRASE:
                raise VaultSealedError(
                    "vault master key is passphrase-wrapped; call unseal with the passphrase"
                )
            try:
                key, _method = keywrap.unwrap(blob)
            except keywrap.KeyWrapError as exc:
                # Every stored secret is unreadable from here. Say why, and
                # point at the backup, rather than surfacing a bare stack trace.
                raise VaultCryptoError(
                    f"cannot unwrap the vault master key: {exc}. If this vault "
                    f"was created by a different Windows account, sign in as "
                    f"that account. A pre-migration copy may exist at "
                    f"{plaintext_backup_path(key_path)}"
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
    """Encrypt/decrypt vault payloads with a local Fernet master key.

    When the on-disk wrap is ``passphrase-scrypt``, construction leaves the
    seal **sealed** (no key in memory) until :meth:`unseal`. DPAPI/plain wraps
    auto-unseal on load so existing vaults keep working.
    """

    def __init__(self, master_key: bytes | None = None, *, key_path: Path | None = None) -> None:
        self._key_path = key_path if key_path is not None else master_key_path()
        self._master_key: bytes | None = None
        self._fernet: Fernet | None = None
        self._wrap_method: str | None = None

        if master_key is not None:
            self._activate(master_key, wrap_method=None)
            return

        if self._key_path.is_file():
            blob = self._key_path.read_bytes()
            if keywrap.is_wrapped(blob):
                method = keywrap.peek_method(blob)
                self._wrap_method = method
                if method == keywrap.METHOD_PASSPHRASE:
                    # Stay sealed until unseal(passphrase).
                    return
            # DPAPI / plain / legacy: load into memory now.
            raw = _load_or_create_master_key(self._key_path)
            method = (
                keywrap.peek_method(self._key_path.read_bytes())
                if self._key_path.is_file()
                else keywrap.preferred_method()
            )
            self._activate(raw, wrap_method=method)
            return

        raw = _load_or_create_master_key(self._key_path)
        method = keywrap.peek_method(self._key_path.read_bytes())
        self._activate(raw, wrap_method=method)

    def _activate(self, key: bytes, *, wrap_method: str | None) -> None:
        self._master_key = key
        self._fernet = Fernet(key)
        if wrap_method is not None:
            self._wrap_method = wrap_method

    @property
    def is_sealed(self) -> bool:
        return self._fernet is None

    @property
    def passphrase_configured(self) -> bool:
        return self._wrap_method == keywrap.METHOD_PASSPHRASE

    @property
    def wrap_method(self) -> str | None:
        return self._wrap_method

    def status(self) -> dict[str, object]:
        return {
            "sealed": self.is_sealed,
            "passphrase_configured": self.passphrase_configured,
            "wrap_method": self._wrap_method,
            # Independent of sealed: a leftover bak is a copy-open hole even
            # while the live wrap is in memory (DR-0010 / R-0011 CSS trap).
            "plaintext_backup_present": plaintext_backup_present(self._key_path),
        }

    def _require_open(self) -> Fernet:
        if self._fernet is None:
            raise VaultSealedError(
                "vault is sealed; POST /api/vault/unseal with the passphrase first"
            )
        return self._fernet

    def encrypt(self, plaintext: str) -> bytes:
        return self._require_open().encrypt(plaintext.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        try:
            return self._require_open().decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise VaultCryptoError("unable to decrypt vault secret") from exc

    def unseal(self, passphrase: str = "") -> None:
        """Load the master key into memory.

        Passphrase-wrapped vaults require the passphrase. DPAPI/plain vaults
        ignore it and re-load from disk (used after :meth:`lock`).
        """
        if self._fernet is not None:
            return

        if not self._key_path.is_file():
            raise VaultCryptoError("no master key file to unseal")

        blob = self._key_path.read_bytes()
        method = keywrap.peek_method(blob) if keywrap.is_wrapped(blob) else keywrap.METHOD_PLAIN
        try:
            if method == keywrap.METHOD_PASSPHRASE:
                key, used = keywrap.unwrap(blob, passphrase=passphrase)
            else:
                key, used = keywrap.unwrap(blob)
        except keywrap.KeyWrapError as exc:
            raise VaultCryptoError(str(exc)) from exc
        self._activate(key, wrap_method=used)
        log.info("vault_unsealed", wrap_method=used)

    def lock(self) -> None:
        """Drop in-process key material. Passphrase vaults stay sealed until unseal."""
        if self._master_key is not None:
            # Best-effort zeroize; Python strings/bytes are immutable so this is
            # advisory only, but it stops casual retention via the attribute.
            self._master_key = b"\x00" * len(self._master_key)
        self._master_key = None
        self._fernet = None
        log.info("vault_locked", passphrase_configured=self.passphrase_configured)

    def set_passphrase(self, passphrase: str) -> None:
        """Rewrap the live master key under a passphrase. Vault must be unsealed."""
        if self.is_sealed or self._master_key is None:
            raise VaultSealedError("cannot set passphrase while the vault is sealed")
        if not passphrase:
            raise VaultCryptoError("passphrase must be non-empty")
        method = _write_key_file(
            self._key_path,
            self._master_key,
            method=keywrap.METHOD_PASSPHRASE,
            passphrase=passphrase,
        )
        self._wrap_method = method
        if plaintext_backup_present(self._key_path):
            # Warn, do not refuse: the operator still needs a working wrap.
            log.warning(
                "plaintext_master_key_backup_present",
                path=str(plaintext_backup_path(self._key_path)),
            )
        log.info("vault_passphrase_configured", wrap_method=method)

    def retire_plaintext_backup(self, *, passphrase: str = "") -> None:
        """Verify the bak matches the live wrapped key, then delete it.

        DR-0010 option (c). Unwraps the on-disk wrap via ``keywrap.unwrap``
        (not the in-memory copy) and byte-compares to the bak. Mismatch
        refuses so a tampered bak cannot be silently discarded.
        """
        if self.is_sealed or self._master_key is None:
            raise VaultSealedError("cannot retire plaintext backup while the vault is sealed")
        bak = plaintext_backup_path(self._key_path)
        if not bak.is_file():
            raise VaultCryptoError("no plaintext backup to retire")
        if not self._key_path.is_file():
            raise VaultCryptoError("live master key file is missing; refusing to retire bak")
        blob = self._key_path.read_bytes()
        if not keywrap.is_wrapped(blob):
            raise VaultCryptoError("live master key is not wrapped; refusing to retire bak")
        method = keywrap.peek_method(blob)
        try:
            if method == keywrap.METHOD_PASSPHRASE:
                live, _used = keywrap.unwrap(blob, passphrase=passphrase)
            else:
                live, _used = keywrap.unwrap(blob)
        except keywrap.KeyWrapError as exc:
            raise VaultCryptoError(str(exc)) from exc
        bak_bytes = bak.read_bytes()
        if live != bak_bytes and live != bak_bytes.strip():
            raise VaultCryptoError(
                "plaintext backup does not match the live wrapped key; refusing to delete"
            )
        bak.unlink()
        log.info("plaintext_master_key_backup_retired", path=str(bak))


def mask_secret(secret: str, *, visible: int = 4) -> str:
    """Return a masked preview suitable for API responses and logs."""
    if not secret:
        return ""
    if len(secret) <= visible * 2:
        return "*" * len(secret)
    return f"{secret[:visible]}…{'*' * min(8, len(secret) - visible)}"
