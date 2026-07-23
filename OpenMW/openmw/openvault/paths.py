"""Filesystem locations for the local OpenVault store."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


def openvault_home() -> Path:
    """Return the OpenVault data directory (override with OPENVAULT_HOME)."""
    override = os.environ.get("OPENVAULT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".openvault"


def ensure_home() -> Path:
    """Create ``~/.openvault`` with restrictive permissions if missing."""
    home = openvault_home()
    home.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        home.chmod(0o700)
    return home


def keys_db_path() -> Path:
    return ensure_home() / "keys.db"


def master_key_path() -> Path:
    return ensure_home() / "master.key"


def selection_path() -> Path:
    return ensure_home() / "orchestration.json"


def fallback_path() -> Path:
    return ensure_home() / "fallback.json"
