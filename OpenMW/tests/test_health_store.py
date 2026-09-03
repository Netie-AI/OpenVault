"""Precheck history store + GET /api/keys/{id}/health (CARD_HEALTH_HISTORY H1/H2)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.health_store import (
    HEARTBEAT_INTERVAL_S,
    MAX_ROWS_PER_KEY,
    HealthStore,
    HistoryStatus,
)
from openmw.openvault.vault.store import KeyVault


@pytest.fixture()
def vault(tmp_path: Path) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


@pytest.fixture()
def health(vault: KeyVault) -> HealthStore:
    return HealthStore(db_path=vault.db_path)


def test_pruning_caps_at_2000_after_5000_inserts(health: HealthStore) -> None:
    key_id = "prune-key-aaaaaaaaaaaaaaaaaaaaaa"
    base = time.time()
    rows: list[tuple[str, float, HistoryStatus, float | None, str | None]] = [
        (key_id, base + i, "ok", 10.0 + (i % 50), None) for i in range(5000)
    ]
    health.bulk_insert(rows)
    assert health.count_for_key(key_id) == 5000
    health.prune(key_id, now=base + 5000)
    assert health.count_for_key(key_id) <= MAX_ROWS_PER_KEY
    assert health.count_for_key(key_id) == MAX_ROWS_PER_KEY


def test_heartbeat_skips_duplicate_status_within_15m(health: HealthStore) -> None:
    key_id = "hb-key-bbbbbbbbbbbbbbbbbbbbbbbb"
    t0 = 1_700_000_000.0
    assert health.record(key_id, "ok", latency_ms=5.0, checked_at=t0) is True
    assert health.record(key_id, "ok", latency_ms=6.0, checked_at=t0 + 60.0) is False
    assert health.count_for_key(key_id) == 1
    assert (
        health.record(
            key_id,
            "ok",
            latency_ms=7.0,
            checked_at=t0 + HEARTBEAT_INTERVAL_S,
        )
        is True
    )
    assert health.count_for_key(key_id) == 2


def test_status_transition_writes_immediately(health: HealthStore) -> None:
    key_id = "tr-key-cccccccccccccccccccccccc"
    t0 = 1_700_000_100.0
    assert health.record(key_id, "ok", latency_ms=5.0, checked_at=t0) is True
    assert (
        health.record(key_id, "auth_fail", latency_ms=None, error="HTTP 401", checked_at=t0 + 1.0)
        is True
    )
    assert health.count_for_key(key_id) == 2


def test_uptime_null_with_fewer_than_3_samples(health: HealthStore) -> None:
    key_id = "up-key-dddddddddddddddddddddddd"
    t0 = time.time()
    health.record(key_id, "ok", latency_ms=10.0, checked_at=t0, force=True)
    summary = health.summarize(key_id, window="24h", now=t0 + 1.0)
    assert len(summary.samples) == 1
    assert summary.uptime_pct is None


def test_rate_limit_counts_as_up_not_failure(health: HealthStore) -> None:
    key_id = "rl-key-eeeeeeeeeeeeeeeeeeeeeeee"
    t0 = time.time()
    for i, status in enumerate(("ok", "rate_limit", "ok")):
        health.record(key_id, status, latency_ms=20.0, checked_at=t0 + i, force=True)
    summary = health.summarize(key_id, window="24h", now=t0 + 10.0)
    assert summary.uptime_pct == 100.0
    assert summary.rate_limit_count == 1


def test_health_endpoint_shape(vault: KeyVault) -> None:
    record = vault.create(
        label="health-shape",
        provider="openai",
        secret="sk-test-secret-value-123456",
        role="primary",
        base_url="https://example.com/v1",
    )
    store = HealthStore(db_path=vault.db_path)
    t0 = time.time()
    store.record(record.id, "ok", latency_ms=12.5, checked_at=t0, force=True)

    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    client = TestClient(app)
    resp = client.get(f"/api/keys/{record.id}/health?window=24h")
    assert resp.status_code == 200
    body = resp.json()
    assert body["key_id"] == record.id
    assert body["window"] == "24h"
    assert isinstance(body["samples"], list)
    assert len(body["samples"]) == 1
    assert body["samples"][0]["status"] == "ok"
    assert body["samples"][0]["latency_ms"] == 12.5
    assert "t" in body["samples"][0]
    assert body["uptime_pct"] is None  # <3 samples
    assert "p50_latency_ms" in body
    assert "p95_latency_ms" in body
    assert body["current_status"] == "ok"
    assert "last_change_at" in body
    assert body["rate_limit_count"] == 0


def test_health_endpoint_404_unknown_key(vault: KeyVault) -> None:
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    client = TestClient(app)
    resp = client.get("/api/keys/does-not-exist/health")
    assert resp.status_code == 404
