"""Platform key wrapping for the vault master key.

The problem this solves: ``master.key`` was a raw Fernet key written in
plaintext next to ``keys.db``, and the ``chmod(0o600)`` guarding it is a no-op
on NTFS. Copying those two files to another machine recovered every secret.

Wrapping the master key with DPAPI (user scope) makes the pair useless off the
machine and outside the user's account, because the unwrap key is derived from
the user's logon credential and held by the OS, never by us. It is not a
passphrase — a process already running as the user can still unwrap — but it
closes the "copied the folder" path at zero cost to the user, which is why it
ships before the passphrase work.

Format, so the file is self-describing rather than guessed at:

    OVK1
    <method>
    <base64 wrapped key>

``method`` is ``dpapi-user`` or ``plain``. A file with no header is a legacy
v0 raw Fernet key and is migrated on first open.
"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes
from typing import Final

import structlog

log = structlog.get_logger()

MAGIC: Final = b"OVK1"
METHOD_DPAPI: Final = "dpapi-user"
METHOD_PLAIN: Final = "plain"

#: Never let DPAPI pop UI — this runs inside a local API server with no desktop
#: session guaranteed, and a modal prompt there would hang the request forever.
_CRYPTPROTECT_UI_FORBIDDEN: Final = 0x01

#: Bound to this application so another program running as the same user cannot
#: unwrap the blob by simply calling CryptUnprotectData on the bytes. Changing
#: this value makes every existing wrapped key unreadable, so it is frozen.
_ENTROPY: Final = b"openvault.vault.master-key.v1"


class KeyWrapError(RuntimeError):
    """Raised when a key cannot be wrapped or unwrapped."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    """Build a DATA_BLOB **and return its backing buffer**.

    The buffer must be returned, not just referenced: a `_DataBlob` built from
    a local `create_string_buffer` holds only a raw pointer, so if the buffer
    is garbage-collected before the syscall runs, DPAPI reads freed memory.
    Callers keep the returned array alive for the duration of the call.
    """
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _blob_bytes(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _free(blob: _DataBlob) -> None:
    if blob.pbData:
        ctypes.windll.kernel32.LocalFree(blob.pbData)  # type: ignore[attr-defined]


def dpapi_available() -> bool:
    return sys.platform == "win32"


def dpapi_protect(data: bytes) -> bytes:
    """Wrap bytes with DPAPI, user scope."""
    if not dpapi_available():
        raise KeyWrapError("DPAPI is only available on Windows")

    out = _DataBlob()
    # `_in_buf` / `_ent_buf` are unused by name but must stay in scope — they
    # own the memory the blobs point at.
    in_blob, _in_buf = _blob(data)
    ent_blob, _ent_buf = _blob(_ENTROPY)
    ok = ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise KeyWrapError(
            f"CryptProtectData failed (error {ctypes.GetLastError()})"  # type: ignore[attr-defined]
        )
    try:
        return _blob_bytes(out)
    finally:
        _free(out)


def dpapi_unprotect(data: bytes) -> bytes:
    """Unwrap bytes previously wrapped by :func:`dpapi_protect`."""
    if not dpapi_available():
        raise KeyWrapError("DPAPI is only available on Windows")

    out = _DataBlob()
    in_blob, _in_buf = _blob(data)
    ent_blob, _ent_buf = _blob(_ENTROPY)
    ok = ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        # The common causes are all recoverable-by-explanation, and the user
        # needs to know which: a different Windows account, a restored profile,
        # or a file copied from another machine. Silence here looks like data
        # corruption.
        raise KeyWrapError(
            "CryptUnprotectData failed (error "
            f"{ctypes.GetLastError()}) — this master key was wrapped by a "  # type: ignore[attr-defined]
            "different Windows user account or on a different machine"
        )
    try:
        return _blob_bytes(out)
    finally:
        _free(out)


def preferred_method() -> str:
    """The strongest wrapping this platform supports."""
    return METHOD_DPAPI if dpapi_available() else METHOD_PLAIN


def wrap(key: bytes, *, method: str | None = None) -> bytes:
    """Serialise a master key into the versioned on-disk format."""
    chosen = method or preferred_method()
    if chosen == METHOD_DPAPI:
        payload = dpapi_protect(key)
    elif chosen == METHOD_PLAIN:
        # Non-Windows, for now. Stored base64 so the file format stays uniform
        # and a future migration can tell v1-plain from v0-raw without guessing.
        payload = key
    else:
        raise KeyWrapError(f"unknown wrap method: {chosen}")
    return b"\n".join([MAGIC, chosen.encode("ascii"), base64.b64encode(payload)])


def unwrap(blob: bytes) -> tuple[bytes, str]:
    """Parse the on-disk format. Returns (key, method).

    A blob with no magic header is a legacy v0 raw Fernet key; it is returned
    as-is with method ``plain`` so the caller can decide to migrate. Guessing
    wrong here would make every stored secret undecryptable, so the check is
    on the header rather than on the length or shape of the bytes.
    """
    if not blob.startswith(MAGIC):
        return blob.strip(), METHOD_PLAIN

    parts = blob.split(b"\n", 2)
    if len(parts) != 3:
        raise KeyWrapError("master key file is truncated or corrupt")

    method = parts[1].decode("ascii", errors="replace").strip()
    try:
        payload = base64.b64decode(parts[2].strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise KeyWrapError("master key payload is not valid base64") from exc

    if method == METHOD_DPAPI:
        return dpapi_unprotect(payload), METHOD_DPAPI
    if method == METHOD_PLAIN:
        return payload, METHOD_PLAIN
    raise KeyWrapError(f"unknown wrap method in master key file: {method!r}")


def is_wrapped(blob: bytes) -> bool:
    """True when the blob is already in the versioned format."""
    return blob.startswith(MAGIC)


__all__ = [
    "METHOD_DPAPI",
    "METHOD_PLAIN",
    "KeyWrapError",
    "dpapi_available",
    "dpapi_protect",
    "dpapi_unprotect",
    "is_wrapped",
    "preferred_method",
    "unwrap",
    "wrap",
]
