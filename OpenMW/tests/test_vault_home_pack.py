"""Sealed OPENVAULT_HOME pack for another laptop (F31). No decrypt. No CSV."""

from __future__ import annotations

import importlib.util
import json
import re
import zipfile
from pathlib import Path

import pytest

PACK_PY = Path(__file__).resolve().parents[2] / "apps" / "cli" / "vault_home_pack.py"


def _load():
    spec = importlib.util.spec_from_file_location("vault_home_pack", PACK_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _passphrase_home(tmp: Path) -> Path:
    home = tmp / "home"
    home.mkdir()
    (home / "master.key").write_bytes(b"OVK1\npassphrase-scrypt\ndGVzdA==\n")
    (home / "keys.db").write_bytes(b"not-a-real-db")
    staged = home / "import"
    staged.mkdir()
    (staged / "leak.keys.env").write_text("OPENAI_API_KEY=sk-live-should-not-pack\n")
    (home / "master.key.v0.bak").write_bytes(b"raw")
    return home


def test_refuses_plaintext_bak(tmp_path: Path) -> None:
    mod = _load()
    home = _passphrase_home(tmp_path)
    with pytest.raises(mod.PackError, match=re.escape("master.key.v0.bak")):
        mod.pack_home(home, tmp_path / "out.zip")


def test_refuses_dpapi(tmp_path: Path) -> None:
    mod = _load()
    home = tmp_path / "dpapi"
    home.mkdir()
    (home / "master.key").write_bytes(b"OVK1\ndpapi-user\ndGVzdA==\n")
    (home / "keys.db").write_bytes(b"x")
    with pytest.raises(mod.PackError, match="dpapi-user"):
        mod.pack_home(home, tmp_path / "out.zip")


def test_pack_round_trip_skips_import_and_bak(tmp_path: Path) -> None:
    mod = _load()
    home = _passphrase_home(tmp_path)
    (home / "master.key.v0.bak").unlink()
    zpath = tmp_path / "home.ovpack.zip"
    manifest = mod.pack_home(home, zpath)
    assert manifest["wrap_method"] == "passphrase-scrypt"
    assert "keys.db" in manifest["files"]
    names = list(manifest["files"])
    assert not any("import" in n or n.endswith(".bak") for n in names)

    dest = tmp_path / "laptop2"
    result = mod.unpack_home(zpath, dest)
    assert Path(result["home"]) == dest.resolve()
    assert (dest / "keys.db").read_bytes() == b"not-a-real-db"
    assert not (dest / "import" / "leak.keys.env").exists()
    assert (dest / "master.key").read_bytes().startswith(b"OVK1")


def test_unpack_refuses_occupied_home(tmp_path: Path) -> None:
    mod = _load()
    home = _passphrase_home(tmp_path)
    (home / "master.key.v0.bak").unlink()
    zpath = tmp_path / "home.ovpack.zip"
    mod.pack_home(home, zpath)
    dest = tmp_path / "taken"
    dest.mkdir()
    (dest / "keys.db").write_bytes(b"old")
    with pytest.raises(mod.PackError, match=re.escape("already has keys.db")):
        mod.unpack_home(zpath, dest)
    mod.unpack_home(zpath, dest, force=True)
    assert (dest / "keys.db").read_bytes() == b"not-a-real-db"


def test_unpack_refuses_zip_slip(tmp_path: Path) -> None:
    mod = _load()
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(
            "openvault-home-pack.json",
            json.dumps({"wrap_method": "passphrase-scrypt"}),
        )
        zf.writestr("../escape.key", "nope")
    with pytest.raises(mod.PackError, match="unsafe path"):
        mod.unpack_home(zpath, tmp_path / "out")
