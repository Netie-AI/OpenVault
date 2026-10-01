"""Per-hop attempt ledger: no body, retention prune, connections close."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from openmw.openvault.route.breaker import reset_all_circuit_breakers
from openmw.openvault.vault import hop_attempts as hop_mod
from openmw.openvault.vault import openrouter_probe as probe_mod
from openmw.openvault.vault import quota as quota_mod
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.hop_attempts import load_hop_attempts, record_hop_attempt
from openmw.openvault.vault.openrouter_probe import load_openrouter_probes, save_openrouter_probe
from openmw.openvault.vault.proxy import chat_completions
from openmw.openvault.vault.quota import tokens_used_for_key
from openmw.openvault.vault.store import KeyVault

_GROQ = "https://api.groq.com/openai/v1"
_SECRET = "sk-test-ov93-hop-not-live"
_REQ = "REQBODY-ov93-unique"
_MARKER = "RESPBODY-ov93-unique"
_MODEL = "openai/gpt-oss-120b"


class _CloseRecorder:
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


@pytest.fixture(autouse=True)
def _reset_breakers() -> Any:
    reset_all_circuit_breakers()
    yield
    reset_all_circuit_breakers()


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KeyVault:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENVAULT_LOCAL_BASE_URL", raising=False)
    return KeyVault(db_path=tmp_path / "keys.db", seal=Seal(Fernet.generate_key()))


def test_hop_attempt_row_contains_no_body(vault: KeyVault) -> None:
    record = vault.create(
        label="groq",
        provider="groq",
        secret=_SECRET,
        role="free",
        priority=1,
        base_url=_GROQ,
    )
    vault.set_precheck(record.id, status="ok", latency_ms=1.0, error=None)
    raw = json.dumps({"error": {"message": f"nope {_MARKER}", "secret": _SECRET}})
    resp = MagicMock()
    resp.status_code = 500
    resp.text = raw
    resp.headers = {}
    resp.json = MagicMock(return_value={"id": "x", "body": _MARKER})
    mock = MagicMock()
    mock.post = AsyncMock(return_value=resp)
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    body = {"model": _MODEL, "messages": [{"role": "user", "content": _REQ}]}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        status, _payload = asyncio.run(chat_completions(vault, FallbackManager(vault), body))
    assert status >= 400
    rows = load_hop_attempts(vault.db_path)
    assert len(rows) == 1
    row = rows[0]
    blob = " ".join([row.request_id, row.key_id, row.model, row.status, row.reason, str(row.ts)])
    assert _MARKER not in blob
    assert _REQ not in blob
    assert _SECRET not in blob
    assert "nope" not in blob
    assert "secret" not in blob
    assert row.status == "500"
    assert row.reason == "transient"
    assert row.model == _MODEL
    assert row.key_id == record.id
    assert row.request_id
    assert row.latency_ms is not None
    assert row.latency_ms >= 0
    with sqlite3.connect(str(vault.db_path)) as conn:
        names = [str(item[1]) for item in conn.execute("PRAGMA table_info(hop_attempts)")]
    assert "body" not in names
    assert names == [
        "request_id",
        "key_id",
        "model",
        "status",
        "latency_ms",
        "reason",
        "ts",
    ]


def test_hop_attempt_retention_prunes_by_age_and_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    age_db = tmp_path / "age.db"
    monkeypatch.setattr(hop_mod, "HOP_ATTEMPT_RETENTION_S", 100)
    monkeypatch.setattr(hop_mod, "HOP_ATTEMPT_MAX_ROWS", 100)
    record_hop_attempt(
        age_db,
        request_id="old",
        key_id="k",
        model="m",
        status="500",
        latency_ms=1,
        reason="transient",
        ts=1000,
        now=1000,
    )
    record_hop_attempt(
        age_db,
        request_id="new",
        key_id="k",
        model="m",
        status="500",
        latency_ms=1,
        reason="transient",
        ts=1300,
        now=1300,
    )
    assert [row.request_id for row in load_hop_attempts(age_db)] == ["new"]

    cap_db = tmp_path / "cap.db"
    monkeypatch.setattr(hop_mod, "HOP_ATTEMPT_RETENTION_S", 10_000)
    monkeypatch.setattr(hop_mod, "HOP_ATTEMPT_MAX_ROWS", 2)
    base = 1_700_000_000.0
    for i in range(4):
        record_hop_attempt(
            cap_db,
            request_id=f"r{i}",
            key_id="k",
            model="m",
            status="200",
            latency_ms=i,
            reason="",
            ts=base + i,
            now=base + i,
        )
    assert [row.request_id for row in load_hop_attempts(cap_db)] == ["r2", "r3"]


def test_hop_attempt_connections_are_closed(tmp_path: Path) -> None:
    db = tmp_path / "keys.db"
    opens: list[int] = []
    closes: list[int] = []
    rounds = 25
    per_round = 5
    before = _db_fd_count(db)
    proxy = _ConnectProxy(opens, closes)
    with (
        patch.object(hop_mod, "sqlite3", proxy),
        patch.object(probe_mod, "sqlite3", proxy),
        patch.object(quota_mod, "sqlite3", proxy),
    ):
        for i in range(rounds):
            record_hop_attempt(
                db,
                request_id=f"req-{i}",
                key_id="key-ov93",
                model="m",
                status="200",
                latency_ms=i,
                reason="ok",
            )
            assert load_hop_attempts(db)
            save_openrouter_probe(
                db,
                key_id="key-ov93",
                limit_remaining=float(i),
                is_free_tier=i % 2 == 0,
            )
            assert "key-ov93" in load_openrouter_probes(db)
            assert tokens_used_for_key(db, provider="groq", vault_key_id="key-ov93", since=0) == 0
            open_now = _db_fd_count(db)
            if open_now is not None:
                assert open_now == 0
    assert len(opens) == rounds * per_round
    assert len(closes) == len(opens)
    if before is not None:
        assert _db_fd_count(db) == 0
