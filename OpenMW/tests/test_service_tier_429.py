"""OV-FREEROUTE-TIER-429-01: service tier, and limited_by on a limiter 429.

A free key at 21 concurrent chat calls is refused by TokenBudgetLimiter before
any hop. A service key at the same offer is not. The usage row stores which
bucket refused. No existing key is retiered by this module.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openmw.cli import app as cli_app
from openmw.openvault.app import create_app
from openmw.openvault.vault.api_keys import TOKEN_PREFIX, ApiKeyStore
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.ratelimit import DEFAULT_TIERS, TokenBudgetLimiter
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.usage_store import UsageEvent, UsageStore

_CHAT = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
_OFFER = 21
runner = CliRunner()


def _secret_absent(blob: str, secret: str) -> None:
    if secret and secret in blob:
        raise AssertionError("command output contained a key value")


def _hop_count(db: Path) -> int:
    if not db.is_file():
        return 0
    with sqlite3.connect(db) as conn:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hop_attempts'"
        ).fetchone()
        if found is None:
            return 0
        return int(conn.execute("SELECT COUNT(*) FROM hop_attempts").fetchone()[0])


def _post_one(app: object, headers: dict[str, str], gate: threading.Barrier) -> httpx.Response:
    gate.wait(timeout=30)

    async def _send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            return await http.post("/v1/chat/completions", json=_CHAT, headers=dict(headers))

    return asyncio.run(_send())


def _burst(app: object, headers: dict[str, str]) -> list[httpx.Response]:
    gate = threading.Barrier(_OFFER)
    with ThreadPoolExecutor(max_workers=_OFFER) as pool:
        futures = [pool.submit(_post_one, app, headers, gate) for _ in range(_OFFER)]
        return [fut.result(timeout=30) for fut in futures]


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.delenv("OPENVAULT_REDIS_URL", raising=False)
    monkeypatch.delenv("OPENVAULT_LOCAL_BASE_URL", raising=False)
    monkeypatch.delenv("OPENVAULT_ROUTE_POLICY", raising=False)
    return root


@pytest.fixture()
def vault(home: Path) -> KeyVault:
    return KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))


def test_service_tier_matches_pro_shape() -> None:
    service = DEFAULT_TIERS["service"]
    pro = DEFAULT_TIERS["pro"]
    assert (service.requests_per_min, service.tokens_per_min) == (120.0, 400_000.0)
    assert (service.requests_per_min, service.tokens_per_min) == (
        pro.requests_per_min,
        pro.tokens_per_min,
    )
    assert DEFAULT_TIERS["free"].requests_per_min == 20
    assert DEFAULT_TIERS["local"].requests_per_min == 6000
    limiter = TokenBudgetLimiter()
    assert limiter.smallest_tier().name == "free"
    assert limiter.tier_for("service").name == "service"
    assert "service" in limiter.known_tiers()


def test_free_burst_429s_before_hop_service_burst_does_not(vault: KeyVault, home: Path) -> None:
    """21 concurrent calls. Free capacity is 20 rpm. Service matches pro at 120."""
    limiter = TokenBudgetLimiter(clock=lambda: 0.0)
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
        rate_limiter=limiter,
    )
    with TestClient(app, client=("127.0.0.1", 5555)) as client:
        free_id, free_headers = issue_key(client, tier="free", label="free-seat")
        service_id, service_headers = issue_key(client, tier="service", label="service-seat")

    free_responses = _burst(app, free_headers)
    service_responses = _burst(app, service_headers)

    denied = [resp for resp in free_responses if resp.status_code == 429]
    assert denied, [resp.status_code for resp in free_responses]
    for resp in denied:
        body = resp.json()["error"]
        assert body["type"] == "rate_limited"
        assert body["limited_by"] == "request"
    assert all(resp.status_code != 429 for resp in service_responses)

    ledger = UsageStore(db_path=home / "keys.db")
    free_rows = [row for row in ledger.events(api_key_id=free_id) if row["status"] == 429]
    assert free_rows
    assert all(row["error_type"] == "rate_limited" for row in free_rows)
    assert all(row["limited_by"] == "request" for row in free_rows)
    service_rows = ledger.events(api_key_id=service_id)
    assert service_rows
    assert all(row["status"] != 429 for row in service_rows)
    assert all(row["limited_by"] == "" for row in service_rows)
    assert _hop_count(vault.db_path) == 0


def test_old_usage_events_gains_limited_by_column(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE usage_events (
              event_id TEXT PRIMARY KEY,
              created_at REAL NOT NULL,
              identity TEXT NOT NULL,
              tier TEXT NOT NULL,
              api_key_id TEXT,
              model_requested TEXT NOT NULL DEFAULT '',
              model_served TEXT NOT NULL DEFAULT '',
              provider TEXT NOT NULL DEFAULT '',
              vault_key_id TEXT NOT NULL DEFAULT '',
              prompt_tokens INTEGER NOT NULL DEFAULT 0,
              completion_tokens INTEGER NOT NULL DEFAULT 0,
              total_tokens INTEGER NOT NULL DEFAULT 0,
              estimated INTEGER NOT NULL DEFAULT 0,
              cache_hit INTEGER NOT NULL DEFAULT 0,
              stream INTEGER NOT NULL DEFAULT 0,
              status INTEGER NOT NULL DEFAULT 0,
              error_type TEXT NOT NULL DEFAULT '',
              latency_ms INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "INSERT INTO usage_events (event_id, created_at, identity, tier, status) "
            "VALUES ('old', 1.0, 'old-id', 'free', 200)"
        )
    store = UsageStore(db_path=db)
    with sqlite3.connect(db) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(usage_events)")]
        old = conn.execute(
            "SELECT identity, tier, status, limited_by FROM usage_events WHERE event_id='old'"
        ).fetchone()
    assert "limited_by" in cols
    assert cols[-4:] == ["served_downgraded", "downgraded_from", "downgraded_to", "cost_status"]
    assert len(cols) == 23
    assert old == ("old-id", "free", 200, "")
    store.record(
        UsageEvent(
            identity="id2",
            tier="free",
            status=429,
            error_type="rate_limited",
            limited_by="token",
        )
    )
    store.record(
        UsageEvent(
            identity="id3",
            tier="free",
            status=429,
            error_type="rate_limited",
            limited_by="not-a-bucket",
        )
    )
    with sqlite3.connect(db) as conn:
        token_row = conn.execute(
            "SELECT limited_by FROM usage_events WHERE identity='id2'"
        ).fetchone()
        dropped = conn.execute(
            "SELECT limited_by FROM usage_events WHERE identity='id3'"
        ).fetchone()
    assert token_row is not None and token_row[0] == "token"
    assert dropped is not None and dropped[0] == ""
    assert store.events(identity="id2")[0]["limited_by"] == "token"


def _row(db: Path, key_id: str) -> dict[str, object]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
    assert row is not None
    return dict(row)


def test_apikey_tier_command_changes_only_tier(home: Path) -> None:
    store = ApiKeyStore(db_path=home / "keys.db")
    record, token = store.issue(label="shared-box", tier="free")
    other, other_token = store.issue(label="leave-me", tier="pro")
    before = _row(home / "keys.db", record.key_id)
    other_before = _row(home / "keys.db", other.key_id)

    result = runner.invoke(cli_app, ["apikey-tier", record.key_id, "service"])

    assert result.exit_code == 0, result.output
    _secret_absent(result.output, token)
    _secret_absent(result.output, other_token)
    assert f"key_id={record.key_id}" in result.output
    assert "tier=service" in result.output
    after = _row(home / "keys.db", record.key_id)
    assert after["tier"] == "service"
    for column, value in before.items():
        if column != "tier":
            assert after[column] == value
    assert _row(home / "keys.db", other.key_id) == other_before
    verified = store.verify(token)
    assert verified is not None and verified.tier == "service"
    assert store.verify(other_token) is not None


def test_apikey_tier_command_refuses_local_unknown_and_tokens(home: Path) -> None:
    store = ApiKeyStore(db_path=home / "keys.db")
    record, token = store.issue(label="shared-box", tier="free")

    refused = runner.invoke(cli_app, ["apikey-tier", record.key_id, "local"])
    assert refused.exit_code == 2
    _secret_absent(refused.output, token)
    still = store.get(record.key_id)
    assert still is not None and still.tier == "free"

    unknown = runner.invoke(cli_app, ["apikey-tier", record.key_id, "unlimited"])
    assert unknown.exit_code == 2
    still = store.get(record.key_id)
    assert still is not None and still.tier == "free"

    missing = runner.invoke(cli_app, ["apikey-tier", "missing-key", "service"])
    assert missing.exit_code == 1
    assert "no key" in missing.output

    fake = TOKEN_PREFIX + ("a" * 32)
    leaked = runner.invoke(cli_app, ["apikey-tier", fake, "service"])
    assert leaked.exit_code == 2
    _secret_absent(leaked.output, fake)
    still = store.get(record.key_id)
    assert still is not None and still.tier == "free"
