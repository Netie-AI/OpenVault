"""Master-key wrapping and the v0 → v1 migration.

The stakes here are asymmetric: a bug that fails loudly costs a restart, and a
bug that writes an unreadable key file costs the user every secret they own.
So most of what is asserted below is that the unhappy paths stay recoverable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from openmw.openvault.vault import keywrap
from openmw.openvault.vault.crypto import Seal, VaultCryptoError, _load_or_create_master_key

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


class TestFormat:
    def test_wrap_is_self_describing(self) -> None:
        blob = keywrap.wrap(Fernet.generate_key())
        assert blob.startswith(keywrap.MAGIC)
        assert keywrap.is_wrapped(blob)

    def test_round_trip(self) -> None:
        key = Fernet.generate_key()
        unwrapped, method = keywrap.unwrap(keywrap.wrap(key))
        assert unwrapped == key
        assert method == keywrap.preferred_method()

    def test_legacy_raw_key_is_not_mistaken_for_wrapped(self) -> None:
        """A v0 file must come back byte-identical, or every secret is lost."""
        key = Fernet.generate_key()
        assert keywrap.is_wrapped(key) is False
        unwrapped, method = keywrap.unwrap(key)
        assert unwrapped == key
        assert method == keywrap.METHOD_PLAIN

    def test_truncated_file_is_rejected(self) -> None:
        with pytest.raises(keywrap.KeyWrapError):
            keywrap.unwrap(keywrap.MAGIC + b"\ndpapi-user")

    def test_unknown_method_is_rejected(self) -> None:
        import base64

        blob = b"\n".join([keywrap.MAGIC, b"future-hsm", base64.b64encode(b"x")])
        with pytest.raises(keywrap.KeyWrapError):
            keywrap.unwrap(blob)

    def test_bad_base64_is_rejected(self) -> None:
        blob = b"\n".join([keywrap.MAGIC, keywrap.METHOD_PLAIN.encode(), b"not!base64!"])
        with pytest.raises(keywrap.KeyWrapError):
            keywrap.unwrap(blob)


@WINDOWS_ONLY
class TestDpapi:
    def test_protect_unprotect_round_trip(self) -> None:
        secret = b"the master key"
        assert keywrap.dpapi_unprotect(keywrap.dpapi_protect(secret)) == secret

    def test_ciphertext_is_not_the_plaintext(self) -> None:
        secret = Fernet.generate_key()
        assert keywrap.dpapi_protect(secret) != secret

    def test_wrapping_is_the_default_on_windows(self) -> None:
        assert keywrap.preferred_method() == keywrap.METHOD_DPAPI

    def test_entropy_is_required(self) -> None:
        """A blob wrapped with our entropy must not unwrap without it.

        This is what stops another process running as the same user from
        recovering the key by calling CryptUnprotectData on the raw bytes.
        """
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        wrapped = keywrap.dpapi_protect(b"secret")
        buf = ctypes.create_string_buffer(wrapped, len(wrapped))
        blob_in = Blob(len(wrapped), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = Blob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(out)
        )
        assert not ok, "unwrapping without the application entropy must fail"


class TestMasterKeyLifecycle:
    def test_new_vault_writes_a_wrapped_key(self, tmp_path: Path) -> None:
        key_path = tmp_path / "master.key"
        key = _load_or_create_master_key(key_path)
        assert key_path.is_file()
        assert keywrap.is_wrapped(key_path.read_bytes())
        # Reopening must yield the same key or existing ciphertext breaks.
        assert _load_or_create_master_key(key_path) == key

    def test_legacy_key_is_migrated_and_still_decrypts(self, tmp_path: Path) -> None:
        """The migration must not change the key, only how it is stored."""
        key_path = tmp_path / "master.key"
        original = Fernet.generate_key()
        key_path.write_bytes(original)

        sealed = Seal(original).encrypt("sk-live-secret")

        loaded = _load_or_create_master_key(key_path)
        assert loaded == original, "migration must preserve the key itself"
        assert keywrap.is_wrapped(key_path.read_bytes())
        assert (tmp_path / "master.key.v0.bak").is_file(), "migration must leave a backup"

        # The decisive assertion: ciphertext written before migration still opens.
        assert Seal(_load_or_create_master_key(key_path)).decrypt(sealed) == "sk-live-secret"

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        key_path = tmp_path / "master.key"
        original = Fernet.generate_key()
        key_path.write_bytes(original)

        first = _load_or_create_master_key(key_path)
        blob_after_first = key_path.read_bytes()
        second = _load_or_create_master_key(key_path)

        assert first == second == original
        assert key_path.read_bytes() == blob_after_first

    def test_unreadable_wrapped_key_explains_itself(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key from another account must produce guidance, not a stack trace."""
        key_path = tmp_path / "master.key"
        _load_or_create_master_key(key_path)

        def _boom(_data: bytes) -> bytes:
            raise keywrap.KeyWrapError("CryptUnprotectData failed (error 13)")

        monkeypatch.setattr(keywrap, "dpapi_unprotect", _boom)
        monkeypatch.setattr(keywrap, "dpapi_available", lambda: True)
        # Force the wrapped branch regardless of host platform.
        key_path.write_bytes(
            b"\n".join([keywrap.MAGIC, keywrap.METHOD_DPAPI.encode(), b"AAAA"])
        )

        with pytest.raises(VaultCryptoError) as excinfo:
            _load_or_create_master_key(key_path)
        message = str(excinfo.value)
        assert "different Windows account" in message
        assert ".v0.bak" in message

    def test_write_verifies_round_trip_before_replacing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If wrap succeeds but unwrap returns junk, refuse to write."""
        key_path = tmp_path / "master.key"
        monkeypatch.setattr(keywrap, "unwrap", lambda _b: (b"wrong-key", "dpapi-user"))
        with pytest.raises(VaultCryptoError, match="round-trip"):
            _load_or_create_master_key(key_path)

    def test_failed_migration_leaves_the_vault_usable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A machine where wrapping breaks keeps working on the legacy key."""
        key_path = tmp_path / "master.key"
        original = Fernet.generate_key()
        key_path.write_bytes(original)

        def _boom(_key: bytes, **_kw: object) -> bytes:
            raise keywrap.KeyWrapError("no DPAPI here")

        monkeypatch.setattr(keywrap, "wrap", _boom)

        assert _load_or_create_master_key(key_path) == original
        assert key_path.read_bytes() == original, "must not corrupt the legacy key"
