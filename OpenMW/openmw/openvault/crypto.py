"""Local sealing for OpenVault secrets (Fernet + machine master key)."""

from __future__ import annotations

import contextlib
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from openmw.openvault.paths import master_key_path


class VaultCryptoError(RuntimeError):
    """Raised when ciphertext cannot be decrypted."""


def _load_or_create_master_key(path: Path | None = None) -> bytes:
    key_path = path if path is not None else master_key_path()
    if key_path.is_file():
        return key_path.read_bytes().strip()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    with contextlib.suppress(OSError):
        key_path.chmod(0o600)
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
