"""Vault and usage-store sqlite handles close after each call.

Linux checks /proc/self/fd. A close-count recorder wraps sqlite3.connect so
the same test runs on Windows, where that proc file is absent.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cryptography.fernet import Fernet

from openmw.openvault.vault import store as store_mod
from openmw.openvault.vault import usage_store as usage_mod
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.usage_store import UsageEvent, UsageStore


class _CloseRecorder:
    """Counts close() and forwards the sqlite connection. C types hide close."""

    def __init__(self, conn: sqlite3.Connection, closes: list[int]) -> None:
        self._conn = conn
        self._closes = closes

    def close(self) -> None:
        self._closes.append(1)
        self._conn.close()

    def __enter__(self) -> _CloseRecorder:
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool | None:
        result = self._conn.__exit__(exc_type, exc, tb)
        return bool(result) if result is not None else None

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_conn", "_closes"}:
            object.__setattr__(self, name, value)
            return
        setattr(self._conn, name, value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


class _ConnectProxy:
    """Module-local stand-in so only that module's sqlite3.connect is wrapped."""

    def __init__(self, opens: list[int], closes: list[int]) -> None:
        self._opens = opens
        self._closes = closes

    def connect(self, *args: Any, **kwargs: Any) -> _CloseRecorder:
        self._opens.append(1)
        return _CloseRecorder(sqlite3.connect(*args, **kwargs), self._closes)

    def __getattr__(self, name: str) -> Any:
        return getattr(sqlite3, name)


def _db_fd_count(db: Path) -> int | None:
    root = Path("/proc/self/fd")
    if not root.is_dir():
        return None
    needle = str(db.resolve())
    count = 0
    for item in root.iterdir():
        try:
            target = os.readlink(item)
        except OSError:
            continue
        if target == needle or target.startswith(needle + "-"):
            count += 1
    return count


def test_vault_and_usage_connections_are_closed(tmp_path: Path) -> None:
    db = tmp_path / "keys.db"
    vault = KeyVault(db_path=db, seal=Seal(Fernet.generate_key()))
    usage = UsageStore(db_path=db)
    opens: list[int] = []
    closes: list[int] = []
    rounds = 25
    # create (insert + get), list, get, record, events, summary
    per_round = 7
    before = _db_fd_count(db)
    with (
        patch.object(store_mod, "sqlite3", _ConnectProxy(opens, closes)),
        patch.object(usage_mod, "sqlite3", _ConnectProxy(opens, closes)),
    ):
        for i in range(rounds):
            record = vault.create(
                label=f"k{i}",
                provider="openai",
                secret=f"sk-test-ov88-close-{i}",
                role="primary",
                priority=1,
                base_url="https://api.openai.com/v1",
            )
            listed = vault.list_keys()
            assert [row.id for row in listed].count(record.id) == 1
            assert len(listed) == i + 1
            got = vault.get(record.id)
            assert got is not None
            assert got.label == f"k{i}"
            assert got.masked_secret
            event = usage.record(
                UsageEvent(
                    identity="local",
                    tier="free",
                    provider="openai",
                    vault_key_id=record.id,
                    total_tokens=i + 1,
                    status=200,
                )
            )
            rows = usage.events(identity="local")
            assert rows[-1]["event_id"] == event.event_id
            assert len(rows) == i + 1
            summary = usage.summary(identity="local")
            assert summary["requests"] == i + 1
            assert summary["total_tokens"] == sum(range(1, i + 2))
            open_now = _db_fd_count(db)
            if open_now is not None:
                assert open_now == 0
    assert len(opens) == rounds * per_round
    assert len(closes) == len(opens)
    if before is not None:
        assert before == 0
        assert _db_fd_count(db) == 0
