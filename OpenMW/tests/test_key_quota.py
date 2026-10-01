"""T3 slice 2: admin per-key quota view.

No network. No provider secret in the payload. usage_events stays 18 columns.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import inject_admin_credential
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.admin_token import ADMIN_HEADER, ensure_admin_token
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.hop_attempts import record_hop_attempt
from openmw.openvault.vault.key_add import mask_key_id
from openmw.openvault.vault.openrouter_probe import save_openrouter_probe
from openmw.openvault.vault.parks import ensure_park_schema
from openmw.openvault.vault.providers import spendable_for_freeroute
from openmw.openvault.vault.quota import iso_utc, quota_window
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.usage_store import UsageEvent, UsageStore

_GROQ = "https://api.groq.com/openai/v1"
_GOOGLE = "https://generativelanguage.googleapis.com/v1beta/openai"
_OPENROUTER = "https://openrouter.ai/api/v1"
_SECRET = "sk-test-ov93-QUOTA-SECRET"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.delenv("OPENVAULT_ADMIN_TOKEN_PATH", raising=False)
    return root


@pytest.fixture()
def vault(home: Path) -> KeyVault:
    return KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))


@pytest.fixture()
def no_inject() -> Any:
    flag = inject_admin_credential.set(False)
    yield
    inject_admin_credential.reset(flag)


def _app(vault: KeyVault) -> TestClient:
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def _cols(db: Path, table: str) -> list[str]:
    with sqlite3.connect(str(db)) as conn:
        return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _key(vault: KeyVault, *, provider: str, base_url: str, secret: str) -> str:
    record = vault.create(
        label=provider,
        provider=provider,
        secret=secret,
        role="free",
        priority=1,
        base_url=base_url,
    )
    vault.set_precheck(record.id, status="ok", latency_ms=1.0, error=None)
    return record.id


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


def test_quota_view_is_401_without_admin_token(vault: KeyVault, no_inject: None) -> None:
    response = _app(vault).get("/api/keys/quota")
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "unauthorized"


def test_quota_view_is_200_with_admin_token(vault: KeyVault, no_inject: None) -> None:
    token = ensure_admin_token()
    response = _app(vault).get("/api/keys/quota", headers={ADMIN_HEADER: token})
    assert response.status_code == 200
    body = response.json()
    assert body == {"ok": True, "keys": []}
    assert token not in response.text


def test_quota_view_output_contains_no_secret(vault: KeyVault) -> None:
    groq_id = _key(vault, provider="groq", base_url=_GROQ, secret=_SECRET)
    google_id = _key(vault, provider="google", base_url=_GOOGLE, secret="AIza-test-ov93-fake")
    router_id = _key(
        vault,
        provider="openrouter",
        base_url=_OPENROUTER,
        secret="sk-or-v1-test-ov93-not-live",
    )
    store = UsageStore(db_path=vault.db_path)
    store.record(
        UsageEvent(
            identity="local",
            tier="free",
            provider="groq",
            vault_key_id=groq_id,
            total_tokens=10,
            status=200,
        )
    )
    store.record(
        UsageEvent(
            identity="local",
            tier="free",
            provider="groq",
            vault_key_id=groq_id,
            total_tokens=99,
            status=500,
        )
    )
    store.record(
        UsageEvent(
            identity="local",
            tier="free",
            provider="groq",
            vault_key_id="",
            total_tokens=50,
            status=200,
        )
    )
    store.record(
        UsageEvent(
            identity="local",
            tier="free",
            provider="google",
            vault_key_id=google_id,
            total_tokens=4,
            status=200,
        )
    )
    ensure_park_schema(vault.db_path)
    with sqlite3.connect(str(vault.db_path)) as conn:
        conn.execute(
            """
            INSERT INTO hop_parks
              (key_id, model, park_until, reason, error_text, updated_at)
            VALUES (?, '', ?, 'rate_limited', ?, ?)
            """,
            (groq_id, time.time() + 3600, f"slow {_SECRET} down", time.time()),
        )
        conn.execute(
            """
            INSERT INTO hop_parks
              (key_id, model, park_until, reason, error_text, updated_at)
            VALUES (?, '', ?, 'rate_limited', ?, ?)
            """,
            (google_id, time.time() - 30, "earlier limit", time.time()),
        )
    save_openrouter_probe(
        vault.db_path,
        key_id=router_id,
        limit_remaining=4.5,
        is_free_tier=True,
    )

    response = _app(vault).get("/api/keys/quota")
    assert response.status_code == 200
    payload = response.text
    for secret in (_SECRET, "AIza-test-ov93-fake", "sk-or-v1-test-ov93-not-live"):
        assert secret not in payload
    for key_id in (groq_id, google_id, router_id):
        assert key_id not in payload

    body = response.json()
    assert body["ok"] is True
    by_provider = {row["provider"]: row for row in body["keys"]}
    groq = by_provider["groq"]
    google = by_provider["google"]
    router = by_provider["openrouter"]
    expected_fields = {
        "masked_id",
        "provider",
        "tokens_used",
        "daily_limit",
        "reset_at",
        "park_state",
        "park_until",
        "error_text",
        "precheck_status",
        "limit_remaining",
        "is_free_tier",
    }
    assert set(groq) == expected_fields
    assert groq["masked_id"] == mask_key_id(groq_id)
    assert groq["tokens_used"] == 10
    assert groq["daily_limit"] == 200_000
    assert groq["park_state"] == "active"
    assert str(groq["park_until"]).endswith("Z")
    assert groq["error_text"] == "slow [redacted] down"
    assert "[redacted]" in groq["error_text"]
    assert groq["precheck_status"] == "ok"
    assert groq["limit_remaining"] is None
    assert groq["is_free_tier"] is None
    _, groq_reset, _ = quota_window("groq")
    assert groq["reset_at"] == iso_utc(groq_reset)

    assert google["tokens_used"] == 4
    assert google["daily_limit"] is None
    assert google["park_state"] == "expired"
    assert google["error_text"] == "earlier limit"
    _, google_reset, _ = quota_window("google")
    assert google["reset_at"] == iso_utc(google_reset)
    assert google["reset_at"] != groq["reset_at"]

    assert router["masked_id"] == mask_key_id(router_id)
    assert router["tokens_used"] == 0
    assert router["park_state"] == ""
    assert router["park_until"] is None
    assert router["limit_remaining"] == 4.5
    assert router["is_free_tier"] is True


def test_usage_events_stays_18_and_keys_columns_unchanged(vault: KeyVault) -> None:
    keys_before = _cols(vault.db_path, "keys")
    UsageStore(db_path=vault.db_path)
    usage = _cols(vault.db_path, "usage_events")
    assert len(usage) == 18
    _app(vault).get("/api/keys/quota")
    record_hop_attempt(
        vault.db_path,
        request_id="req-ov93",
        key_id="key-ov93",
        model="openai/gpt-oss-120b",
        status="500",
        latency_ms=3,
        reason="transient",
    )
    save_openrouter_probe(
        vault.db_path,
        key_id="key-ov93",
        limit_remaining=1.0,
        is_free_tier=False,
    )
    assert _cols(vault.db_path, "keys") == keys_before
    assert _cols(vault.db_path, "usage_events") == usage
    assert _cols(vault.db_path, "hop_attempts") == [
        "request_id",
        "key_id",
        "model",
        "status",
        "latency_ms",
        "reason",
        "ts",
    ]
    assert _cols(vault.db_path, "openrouter_probe") == [
        "key_id",
        "limit_remaining",
        "is_free_tier",
        "checked_at",
    ]
    assert "body" not in _cols(vault.db_path, "hop_attempts")


def test_spendable_count_unchanged(vault: KeyVault) -> None:
    _key(vault, provider="groq", base_url=_GROQ, secret="gsk-test-ov93-spend")
    body = _app(vault).get("/api/freeroute/status").json()
    assert body["spendable_count"] == len(spendable_for_freeroute())
    assert body["pooled_key_count"] == 1


def test_quota_view_open_handles_do_not_grow(vault: KeyVault) -> None:
    _key(vault, provider="groq", base_url=_GROQ, secret="gsk-test-ov93-handles")
    client = _app(vault)
    before = _db_fd_count(vault.db_path)
    for _ in range(25):
        response = client.get("/api/keys/quota")
        assert response.status_code == 200
        assert len(response.json()["keys"]) == 1
        open_now = _db_fd_count(vault.db_path)
        if open_now is not None:
            assert open_now == 0
    if before is not None:
        assert _db_fd_count(vault.db_path) == 0
