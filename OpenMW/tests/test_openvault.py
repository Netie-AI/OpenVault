"""OpenVault unit tests — vault, fallback, API smoke."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal, mask_secret
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.mesh.orchestration import OrchestrationSelection, load_selection, save_selection
from openmw.openvault.vault.store import KeyVault


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    return tmp_path / "openvault"


@pytest.fixture()
def vault(vault_dir: Path) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=vault_dir / "keys.db", seal=seal)


def test_mask_secret() -> None:
    assert mask_secret("sk-abcdefghijklmnop").startswith("sk-a")
    assert "…" in mask_secret("sk-abcdefghijklmnop")


def test_vault_crud_roundtrip(vault: KeyVault) -> None:
    created = vault.create(
        label="work",
        provider="openai",
        secret="sk-test-secret-value-123456",
        role="primary",
        priority=10,
    )
    assert created.masked_secret != "sk-test-secret-value-123456"
    assert "…" in created.masked_secret or "*" in created.masked_secret
    assert vault.get_secret(created.id) == "sk-test-secret-value-123456"

    updated = vault.update(created.id, role="backup", priority=20)
    assert updated is not None
    assert updated.role == "backup"
    assert updated.priority == 20

    vault.set_precheck(created.id, status="ok", latency_ms=12.5, error=None)
    listed = vault.list_keys()
    assert len(listed) == 1
    assert listed[0].precheck_status == "ok"
    assert listed[0].last_latency_ms == 12.5

    assert vault.delete(created.id) is True
    assert vault.list_keys() == []


def test_fallback_opens_circuit(vault: KeyVault) -> None:
    primary = vault.create(
        label="primary",
        provider="openai",
        secret="sk-primary-aaaaaaaa",
        role="primary",
        priority=1,
        base_url="https://example.com/v1",
    )
    backup = vault.create(
        label="backup",
        provider="openai",
        secret="sk-backup-bbbbbbbb",
        role="backup",
        priority=2,
        base_url="https://example.com/v1",
    )
    vault.set_precheck(primary.id, status="ok", latency_ms=10.0, error=None)
    vault.set_precheck(backup.id, status="ok", latency_ms=11.0, error=None)

    mgr = FallbackManager(vault)
    ordered = mgr.ordered_candidates()
    assert ordered[0].id == primary.id

    mgr.record_failure(primary.id, "HTTP 500")
    mgr.record_failure(primary.id, "HTTP 500")
    mgr.record_failure(primary.id, "HTTP 500")
    ordered2 = mgr.ordered_candidates()
    assert ordered2[0].id == backup.id
    status = mgr.status()
    primary_hop = next(h for h in status.hops if h["key_id"] == primary.id)
    assert primary_hop["circuit"] == "open"


def test_gate_blocks_deploy_without_keys(vault: KeyVault) -> None:
    from openmw.openvault.ship.gate import check_gate

    denied = check_gate(action="deploy", vault=vault)
    assert denied.allowed is False
    vault.create(
        label="groq",
        provider="groq",
        secret="gsk-test-aaaaaaaaaaaa",
        role="free",
    )
    allowed = check_gate(action="deploy", vault=vault, required_providers=["groq"])
    assert allowed.allowed is True


def test_keyvault_snapshot_and_upsert(vault: KeyVault) -> None:
    from openmw.openvault.vault.airgpt_keyvault import keyvault_snapshot, upsert_env_secret

    up = upsert_env_secret(vault, env_key="GROQ_API_KEY", secret="gsk-test-bbbbbbbbbbbb")
    assert up["ok"] is True
    snap = keyvault_snapshot(vault)
    assert snap["source"] == "openvault"
    groq = next(p for p in snap["providers"] if p["id"] == "groq")
    assert groq["configured"] is True


def test_orchestration_selection_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "selection.json"
    saved = save_selection(
        OrchestrationSelection(
            primary_model="llama-3.3-8b",
            cortex_tier="T2",
            engine_id="vllm",
        ),
        path=path,
    )
    loaded = load_selection(path)
    assert loaded.primary_model == saved.primary_model
    assert loaded.cortex_tier == "T2"
    assert loaded.engine_id == "vllm"


def test_api_smoke(vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    # Custody mutations are loopback-only; TestClient's default host is
    # "testclient", which the guard correctly rejects.
    client = TestClient(app, client=("127.0.0.1", 5555))

    z = client.get("/api/healthz")
    assert z.status_code == 200
    assert z.json()["service"] == "openvault"

    devices = client.get("/api/health/devices")
    assert devices.status_code == 200
    assert "devices" in devices.json()

    bottleneck = client.get("/api/health/bottleneck")
    assert bottleneck.status_code == 200
    assert "hop_timeline" in bottleneck.json()

    created = client.post(
        "/api/keys",
        json={
            "label": "mock",
            "provider": "ollama",
            "secret": "local-no-auth",
            "role": "free",
            "base_url": "http://127.0.0.1:11434",
            "priority": 50,
        },
    )
    assert created.status_code == 200
    key_id = created.json()["id"]
    assert "…" in created.json()["masked_secret"] or "*" in created.json()["masked_secret"]

    listed = client.get("/api/keys")
    assert listed.status_code == 200
    assert len(listed.json()["keys"]) == 1

    # Revealing plaintext requires loopback *and* an explicit intent header.
    # This client is loopback (custody mutations above need it), so the missing
    # header is what bites here — 428. The off-loopback 403 and the full matrix
    # live in test_secret_reveal_gate.py.
    gated = client.get(f"/api/keys/{key_id}/secret")
    assert gated.status_code == 428

    fb = client.get("/api/fallback/status")
    assert fb.status_code == 200
    assert "hops" in fb.json()

    models = client.get("/api/cortex/models")
    assert models.status_code == 200
    assert models.json()["local_count"] >= 1

    status = client.get("/api/cortex/status")
    assert status.status_code == 200
    assert status.json()["online"] is False

    sel = client.put(
        "/api/orchestration/selection",
        json={
            "primary_model": "llama-3.3-8b",
            "fallback_models": [],
            "cortex_tier": "T1",
            "engine_id": "ollama",
            "notes": "test",
        },
    )
    assert sel.status_code == 200
    assert sel.json()["primary_model"] == "llama-3.3-8b"

    proxy = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    # No real upstream — expect fallback exhaustion or connection failure path
    assert proxy.status_code in (502, 503)

    assert client.delete(f"/api/keys/{key_id}").status_code == 200
