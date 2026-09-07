"""Carry a sealed OpenVault home to another laptop you own (F28/F31).

Stdlib only: the launcher must not import OpenMW. This copies on-disk files.
It never decrypts keys.db. It is not CSV and not a cloud export.

DPAPI-wrapped homes stay on this Windows user (keywrap.py). Only
passphrase-scrypt travels. Plaintext bak and import/ staging are refused.
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

MAGIC: Final = b"OVK1"
METHOD_PASSPHRASE: Final = "passphrase-scrypt"
BAK_NAME: Final = "master.key.v0.bak"
SKIP_DIR_NAMES: Final = frozenset({"import", "__pycache__"})
SKIP_FILE_NAMES: Final = frozenset(
    {
        BAK_NAME,
        "rust-auth.db",
        "demo_private_key",
    }
)
MANIFEST_NAME: Final = "openvault-home-pack.json"


class PackError(RuntimeError):
    """Closed failure: will not copy a home that can be opened without a passphrase."""

    def __init__(self, message: str, *, status: int = 1) -> None:
        super().__init__(message)
        self.status = status


def wrap_method(blob: bytes) -> str:
    if not blob.startswith(MAGIC):
        return "plain"
    parts = blob.split(b"\n", 2)
    if len(parts) < 2:
        raise PackError("master.key is truncated or corrupt")
    return parts[1].decode("ascii", errors="replace").strip()


def _skip(rel: Path) -> bool:
    if any(part in SKIP_DIR_NAMES for part in rel.parts):
        return True
    if rel.name in SKIP_FILE_NAMES or rel.name.endswith(".bak"):
        return True
    if rel.suffix in {".pyc", ".log"}:
        return True
    return False


def assert_packable(home: Path) -> str:
    """Return wrap method. Refuse anything a thief could open without your passphrase."""
    if not home.is_dir():
        raise PackError(f"OPENVAULT_HOME is missing: {home}")
    bak = home / BAK_NAME
    if bak.is_file():
        raise PackError(
            f"refuse: {BAK_NAME} is a plaintext master key. Retire it on /vault first."
        )
    key_path = home / "master.key"
    if not key_path.is_file():
        raise PackError(f"refuse: no master.key under {home}")
    method = wrap_method(key_path.read_bytes())
    if method != METHOD_PASSPHRASE:
        raise PackError(
            f"refuse: wrap is {method!r}. Set a passphrase (passphrase-scrypt) "
            "then pack. DPAPI and plain keys do not travel to another laptop."
        )
    return method


def pack_home(home: Path, dest_zip: Path) -> dict[str, object]:
    method = assert_packable(home)
    dest_zip = dest_zip.resolve()
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(home.rglob("*")):
            if not path.is_file():
                continue
            if path.resolve() == dest_zip:
                continue
            rel = path.relative_to(home)
            if _skip(rel):
                continue
            arc = rel.as_posix()
            zf.write(path, arcname=arc)
            names.append(arc)
        manifest = {
            "kind": "openvault-home-pack",
            "wrap_method": method,
            "packed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "file_count": len(names),
            "passkey_travels": False,
            "note": (
                "Sealed home only. Unseal on the other laptop with the same "
                "passphrase. Windows Hello / iPhone passkey stays on this box."
            ),
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
    manifest["zip"] = str(dest_zip)
    manifest["files"] = names
    return manifest


def unpack_home(src_zip: Path, dest: Path, *, force: bool = False) -> dict[str, object]:
    src_zip = src_zip.resolve()
    dest = dest.resolve()
    if not src_zip.is_file():
        raise PackError(f"pack zip missing: {src_zip}")
    marker = dest / "keys.db"
    if marker.is_file() and not force:
        raise PackError(f"refuse: {dest} already has keys.db (pass --force to overwrite)")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src_zip, "r") as zf:
        names = zf.namelist()
        if MANIFEST_NAME not in names:
            raise PackError("refuse: not an OpenVault home pack (missing manifest)")
        raw = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        method = str(raw.get("wrap_method") or "")
        if method != METHOD_PASSPHRASE:
            raise PackError(f"refuse: pack wrap is {method!r}, not passphrase-scrypt")
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.rstrip("/") == MANIFEST_NAME or name.endswith("/"):
                continue
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts:
                raise PackError(f"refuse: unsafe path in zip: {name}")
            if _skip(rel):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                out.write(src.read())
    assert_packable(dest)
    return {"home": str(dest), "wrap_method": method, "from": str(src_zip)}
