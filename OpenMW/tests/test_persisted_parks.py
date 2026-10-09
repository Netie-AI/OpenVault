"""T3 slice 1: parks in keys.db, quota health, scrubbed errors, usable count.

No network. Upstream httpx is mocked. Strict pin keeps pin_unavailable.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.route.breaker import reset_all_circuit_breakers
from openmw.openvault.vault import fallback as fallback_mod
from openmw.openvault.vault import parks as parks_mod
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.parks import (
    ERROR_TEXT_MAX,
    delete_park,
    ensure_park_schema,
    load_parks,
    provider_error_text,
    save_park,
)
from openmw.openvault.vault.providers import spendable_for_freeroute
from openmw.openvault.vault.proxy import PIN_UNAVAILABLE, chat_completions
from openmw.openvault.vault.quota import window_bounds
from openmw.openvault.vault.ratelimit import TierLimits, TokenBudgetLimiter
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.usage_store import HopTrace, UsageEvent, UsageStore

_GROQ = "https://api.groq.com/openai/v1"
_GOOGLE = "https://generativelanguage.googleapis.com/v1beta/openai"
_PIN = "openai/gpt-oss-120b"
_REQ = "REQBODY-ov86-unique"
_BODY_MARKER = "RESPBODY-ov86-unique"
_OV = "ov_" + "SUPERSECRET999"
_SK = "sk-" + "SECRETVALUE123456"
_USAGE_COLUMNS = (
    "event_id",
    "created_at",
    "identity",
    "tier",
    "api_key_id",
    "model_requested",
    "model_served",
    "provider",
    "vault_key_id",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "estimated",
    "cache_hit",
    "stream",
    "status",
    "error_type",
    "latency_ms",
)


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    reset_all_circuit_breakers()
    yield
    reset_all_circuit_breakers()


@pytest.fixture()
def vault(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> KeyVault:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENVAULT_LOCAL_BASE_URL", raising=False)
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


def _cols(db: Any, table: str) -> list[str]:
    with sqlite3.connect(str(db)) as conn:
        return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _tables(db: Any) -> set[str]:
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def _park_text(db: Any) -> list[str]:
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute("SELECT error_text FROM hop_parks").fetchall()
    return [str(row[0]) for row in rows]


def _key(
    vault: KeyVault,
    *,
    provider: str,
    secret: str,
    base_url: str,
    label: str,
) -> str:
    record = vault.create(
        label=label,
        provider=provider,
        secret=secret,
        role="primary",
        priority=1,
        base_url=base_url,
    )
    vault.set_precheck(record.id, status="ok", latency_ms=5.0, error=None)
    return record.id


def _app(vault: KeyVault, limiter: TokenBudgetLimiter | None = None) -> TestClient:
    kwargs: dict[str, Any] = {
        "vault": vault,
        "mock_health": True,
        "enable_precheck_loop": False,
        "cortex_url": "http://127.0.0.1:9",
    }
    if limiter is not None:
        kwargs["rate_limiter"] = limiter
    app = create_app(**kwargs)
    return TestClient(app, client=("127.0.0.1", 5555))


def _response(status: int, text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.headers = {}
    resp.json = MagicMock(return_value={"id": "x"})
    return resp


def test_usage_events_schema_stays_18_columns(vault: KeyVault) -> None:
    keys_before = _cols(vault.db_path, "keys")
    UsageStore(db_path=vault.db_path)
    usage = _cols(vault.db_path, "usage_events")
    assert usage == list(_USAGE_COLUMNS)
    assert len(usage) == 18
    FallbackManager(vault)
    assert _cols(vault.db_path, "keys") == keys_before
    assert _cols(vault.db_path, "usage_events") == usage
    assert "hop_parks" in _tables(vault.db_path)


def test_park_survives_new_app_instance(vault: KeyVault) -> None:
    key_id = _key(
        vault,
        provider="openai",
        secret="sk-test-ov86-park-aaaa",
        base_url="https://api.openai.com/v1",
        label="openai",
    )
    mgr = FallbackManager(vault)
    mgr.record_park(key_id, 120_000, "rate_limited", error_text="upstream said slow")
    assert mgr.key_is_parked(key_id)

    client = _app(vault)
    body = client.get("/api/freeroute/status").json()
    hop = next(item for item in body["hops"] if item["key_id"] == key_id)
    assert hop["park_state"] == "parked"
    assert hop["park_reason"] == "rate_limited"
    assert hop["error_text"] == "upstream said slow"
    assert float(hop["park_until"]) > time.time()


def test_expired_park_is_routed_again(vault: KeyVault, monkeypatch: pytest.MonkeyPatch) -> None:
    key_id = _key(
        vault,
        provider="openai",
        secret="sk-test-ov86-park-bbbb",
        base_url="https://api.openai.com/v1",
        label="openai",
    )
    mgr = FallbackManager(vault)
    mgr.record_park(key_id, 5_000, "rate_limited")
    until = mgr._circuit(key_id).park_until
    assert until is not None
    assert key_id not in [row.id for row in mgr.ordered_candidates()]

    monkeypatch.setattr(fallback_mod.time, "time", lambda: until + 1.0)
    assert mgr.key_is_parked(key_id) is False
    hop = next(item for item in mgr.status().hops if item["key_id"] == key_id)
    assert hop["park_state"] == "expired"
    restarted = FallbackManager(vault)
    assert key_id in [row.id for row in restarted.ordered_candidates()]
    again = next(item for item in restarted.status().hops if item["key_id"] == key_id)
    assert again["park_state"] == "expired"


def test_google_is_parked_until_quota_resets(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_id = _key(
        vault,
        provider="google",
        secret="AIza-test-ov86-google",
        base_url=_GOOGLE,
        label="google",
    )
    mgr = FallbackManager(vault)
    before = time.time()
    mgr.record_park(key_id, 1_000, "rate_limited")
    until = mgr._circuit(key_id).park_until
    _, reset = window_bounds("America/Los_Angeles", before)
    assert until is not None
    assert abs(until - reset) < 2.0
    assert until - before > 60
    assert mgr.key_is_parked(key_id)
    assert key_id not in [row.id for row in mgr.ordered_candidates()]

    monkeypatch.setattr(fallback_mod.time, "time", lambda: until + 1.0)
    assert mgr.key_is_parked(key_id) is False
    hop = next(item for item in mgr.status().hops if item["key_id"] == key_id)
    assert hop["park_state"] == "expired"
    assert key_id in [row.id for row in mgr.ordered_candidates()]


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

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


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


def test_park_db_connections_are_closed(tmp_path: Any) -> None:
    db = tmp_path / "parks.db"
    ensure_park_schema(db)
    closes: list[int] = []
    real_connect = parks_mod._connect

    def _wrapped(db_path: Path) -> _CloseRecorder:
        return _CloseRecorder(real_connect(db_path), closes)

    rounds = 25
    before = _db_fd_count(db)
    with patch.object(parks_mod, "_connect", _wrapped):
        for i in range(rounds):
            save_park(
                db,
                key_id=f"k{i}",
                model="",
                park_until=time.time() + 30,
                reason="rate_limited",
                error_text="slow",
            )
            saved = load_parks(db)
            assert [row.key_id for row in saved] == [f"k{i}"]
            delete_park(db, f"k{i}")
            assert load_parks(db) == []
            open_now = _db_fd_count(db)
            if open_now is not None:
                assert open_now == 0
    assert len(closes) == rounds * 4
    if before is not None:
        assert _db_fd_count(db) == 0


def test_scrub_redacts_long_hex_and_csk_key() -> None:
    hex_token = "ab" * 20
    csk = "csk-" + "unitfake12"
    xai = "xai-" + "unitfake12"
    message = f"denied {hex_token} {csk} {xai} later"
    raw = json.dumps(
        {
            "error": {"message": message, "body_marker": _BODY_MARKER},
            "blob": hex_token,
        }
    )
    stored = provider_error_text(raw, status=401)
    assert len(stored) <= ERROR_TEXT_MAX
    assert "[redacted]" in stored
    assert hex_token not in stored
    assert "csk-" not in stored
    assert "xai-" not in stored
    assert "denied" in stored
    assert "later" in stored
    assert _BODY_MARKER not in stored
    assert "blob" not in stored


def test_error_text_is_truncated_and_has_no_secrets(vault: KeyVault) -> None:
    _key(
        vault,
        provider="groq",
        secret="gsk-test-ov86-eeee",
        base_url=_GROQ,
        label="groq",
    )
    message = _OV + " " + ("A" * 190) + " " + _SK
    raw = json.dumps({"error": {"message": message, "body_marker": _BODY_MARKER}})
    stored = provider_error_text(raw, status=402)
    assert len(stored) <= ERROR_TEXT_MAX
    assert "sk-" not in stored
    assert "ov_" not in stored
    assert _BODY_MARKER not in stored
    assert "SECRETVALUE" not in stored
    assert "SUPERSECRET" not in stored

    resp = _response(402, raw)
    mock = MagicMock()
    mock.post = AsyncMock(return_value=resp)
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    mgr = FallbackManager(vault)
    body = {"model": "auto", "messages": [{"role": "user", "content": _REQ}]}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        asyncio.run(chat_completions(vault, mgr, body))

    texts = _park_text(vault.db_path)
    assert texts
    assert all(len(item) <= ERROR_TEXT_MAX for item in texts)
    blob = vault.db_path.read_bytes()
    for secret in (_REQ, _BODY_MARKER, "SECRETVALUE123456", "SUPERSECRET999"):
        assert secret.encode() not in blob
        assert secret not in "".join(texts)


def test_all_hops_parked_503_sets_retry_after(vault: KeyVault) -> None:
    key_id = _key(
        vault,
        provider="openai",
        secret="sk-test-ov86-park-cccc",
        base_url="https://api.openai.com/v1",
        label="openai",
    )
    FallbackManager(vault).record_park(key_id, 90_000, "rate_limited")
    client = _app(vault)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503
    message = resp.json()["error"]["message"]
    assert message.startswith("all hops parked, retry at ")
    stamp = message.removeprefix("all hops parked, retry at ")
    assert stamp.endswith("Z")
    assert "T" in stamp
    retry = resp.headers.get("Retry-After")
    assert retry is not None
    assert int(retry) >= 1


def test_own_429_sets_retry_after(vault: KeyVault) -> None:
    limiter = TokenBudgetLimiter(
        tiers={
            "local": TierLimits("local", requests_per_min=1, tokens_per_min=100_000),
            "free": TierLimits("free", requests_per_min=1, tokens_per_min=100_000),
        }
    )
    client = _app(vault, limiter)
    _identity, headers = issue_key(client, tier="free")
    body = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
    first = client.post("/v1/chat/completions", json=body, headers=headers)
    assert first.status_code in (502, 503)
    second = client.post("/v1/chat/completions", json=body, headers=headers)
    assert second.status_code == 429
    assert second.headers.get("Retry-After") is not None
    assert int(second.headers["Retry-After"]) >= 1
    assert second.json()["error"]["type"] == "rate_limited"


def test_usable_provider_count_ignores_park_and_keeps_spendable(vault: KeyVault) -> None:
    groq_id = _key(
        vault,
        provider="groq",
        secret="gsk-test-ov86-ffff",
        base_url=_GROQ,
        label="groq",
    )
    google_id = _key(
        vault,
        provider="google",
        secret="AIza-test-ov86-gggg",
        base_url=_GOOGLE,
        label="google",
    )
    mgr = FallbackManager(vault)
    assert mgr.usable_provider_count() == 2
    mgr.record_park(google_id, 60_000, "rate_limited")
    assert mgr.usable_provider_count() == 1

    client = _app(vault)
    body = client.get("/api/freeroute/status").json()
    spendable = len(spendable_for_freeroute())
    assert body["usable_provider_count"] == 1
    assert body["pooled_key_count"] == 2
    assert body["spendable_count"] == spendable
    assert groq_id != google_id

    UsageStore(db_path=vault.db_path).record(
        UsageEvent(
            identity="local",
            tier="free",
            provider="groq",
            total_tokens=200_000,
            status=200,
        )
    )
    after = client.get("/api/freeroute/status").json()
    assert after["usable_provider_count"] == 0
    assert after["spendable_count"] == spendable
    assert after["pooled_key_count"] == 2


def test_quota_health_flips_when_tokens_pass_the_limit(vault: KeyVault) -> None:
    key_id = _key(
        vault,
        provider="groq",
        secret="gsk-test-ov86-hhhh",
        base_url=_GROQ,
        label="groq",
    )
    store = UsageStore(db_path=vault.db_path)
    store.record(
        UsageEvent(
            identity="local",
            tier="free",
            provider="groq",
            total_tokens=199_999,
            status=200,
        )
    )
    client = _app(vault)
    under = client.get(f"/api/keys/{key_id}/health").json()
    assert under["current_status"] == "ok"

    store.record(
        UsageEvent(
            identity="local",
            tier="free",
            provider="groq",
            total_tokens=1,
            status=200,
        )
    )
    over = client.get(f"/api/keys/{key_id}/health").json()
    assert over["current_status"] == "quota_exhausted"
    assert over["current_status"] != "ok"
    assert over["tokens_used"] == 200_000
    assert over["daily_token_limit"] == 200_000
    assert str(over["quota_reset_at"]).endswith("Z")
    status_body = client.get("/api/freeroute/status").json()
    hop = next(item for item in status_body["hops"] if item["key_id"] == key_id)
    assert hop["health"] == "quota_exhausted"
    assert FallbackManager(vault).ordered_candidates() == []


def test_strict_pin_reuses_persisted_park_and_quota(vault: KeyVault) -> None:
    groq_id = _key(
        vault,
        provider="groq",
        secret="gsk-test-ov86-iiii",
        base_url=_GROQ,
        label="groq",
    )
    _key(
        vault,
        provider="google",
        secret="AIza-test-ov86-jjjj",
        base_url=_GOOGLE,
        label="google",
    )
    FallbackManager(vault).record_park(groq_id, 30_000, "rate_limited")
    mgr = FallbackManager(vault)
    mock = MagicMock()
    mock.post = AsyncMock(side_effect=AssertionError("strict park must not call upstream"))
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    trace = HopTrace()
    body = {"model": _PIN, "messages": [{"role": "user", "content": "hi"}], "strict": True}
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        status, payload = asyncio.run(chat_completions(vault, mgr, body, trace=trace))
    assert status == 503
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == PIN_UNAVAILABLE
    assert payload["error"]["reason"] == "parked"
    assert payload["served_provider"] is None
    assert payload["served_model"] is None
    assert trace.retry_after_s is not None
    assert mock.post.await_count == 0

    clear = FallbackManager(vault)
    clear.record_success(groq_id)
    UsageStore(db_path=vault.db_path).record(
        UsageEvent(
            identity="local",
            tier="free",
            provider="groq",
            total_tokens=200_000,
            status=200,
        )
    )
    quota_mgr = FallbackManager(vault)
    quota_trace = HopTrace()
    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        q_status, q_payload = asyncio.run(
            chat_completions(vault, quota_mgr, body, trace=quota_trace)
        )
    assert q_status == 503
    assert isinstance(q_payload, dict)
    assert q_payload["error"]["type"] == PIN_UNAVAILABLE
    assert q_payload["error"]["reason"] == "quota_exhausted"
    assert q_payload["served_provider"] is None
    assert quota_trace.retry_after_s is not None
    assert mock.post.await_count == 0
