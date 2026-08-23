"""Provider catalog + seed + downtime API tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.providers import catalog_coverage_report, get_provider, list_catalog
from openmw.openvault.vault.seed import seed_essentials
from openmw.openvault.vault.store import KeyVault


def test_catalog_has_openrouter_ollama_litellm() -> None:
    ids = {p["id"] for p in list_catalog()}
    assert "openrouter" in ids
    assert "ollama" in ids
    assert "litellm" in ids
    assert "groq" in ids
    free = list_catalog(free_only=True)
    assert any(p["id"] == "openrouter" for p in free)
    assert all("register_url" in p for p in free)


def test_essentials_for_cortex_airgpt() -> None:
    from openmw.openvault.vault.providers import essentials_for

    rows = essentials_for("cortex", "airgpt")
    ids = {r["id"] for r in rows}
    assert "openrouter" in ids
    assert "openai" in ids
    assert "anthropic" in ids
    assert "ollama" in ids


def test_seed_essentials_local_and_pending(tmp_path: Path) -> None:
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=seal)
    result = seed_essentials(vault, consumers=("cortex", "airgpt", "openvault"))
    assert any(c["provider"] == "ollama" for c in result["created_local"])
    assert any(c["provider"] == "cortex" for c in result["created_local"])
    assert any(p["provider"] == "openrouter" for p in result["pending_register"])
    assert all("register_url" in p for p in result["pending_register"])


def test_coverage_report_lists_missing() -> None:
    report = catalog_coverage_report(set())
    assert report["catalog_size"] >= 10
    assert "openrouter" in report["missing_by_consumer"]["cortex"][0]["id"] or any(
        m["id"] == "openrouter" for m in report["missing_by_consumer"]["cortex"]
    )


def test_providers_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=seal)
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    client = TestClient(app, client=("127.0.0.1", 5555))

    cat = client.get("/api/providers/catalog")
    assert cat.status_code == 200
    assert cat.json()["count"] >= 10

    free = client.get("/api/providers/free")
    assert free.status_code == 200
    assert free.json()["count"] >= 5

    seed = client.post(
        "/api/vault/seed-essentials",
        json={"consumers": ["cortex", "airgpt", "openvault"]},
    )
    assert seed.status_code == 200
    assert len(seed.json()["pending_register"]) >= 1

    cov = client.get("/api/providers/coverage")
    assert cov.status_code == 200
    assert "missing_by_consumer" in cov.json()

    # Local ollama downtime probe (may be offline — still 200)
    down = client.post("/api/providers/ollama/downtime-check")
    assert down.status_code == 200
    assert "online" in down.json()
    assert get_provider("ollama") is not None
