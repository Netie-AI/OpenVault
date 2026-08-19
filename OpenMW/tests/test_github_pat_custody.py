"""GitHub PAT custody in the sealed vault (OpenVault#20).

Acceptance: save stores behind Seal; no recoverable plaintext in pat.json;
resolve works after unseal; clear removes the sealed record; legacy migrate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship import github_auth
from openmw.openvault.ship.github_auth import (
    GITHUB_SHIP_PAT_ID,
    clear_pat,
    resolve_token,
    save_pat,
)
from openmw.openvault.vault.crypto import Seal, VaultSealedError
from openmw.openvault.vault.store import KeyVault

PASSPHRASE = "pat-custody-test-passphrase"
TOKEN = "ghp_test_pat_token_not_real_0001"


def _client() -> TestClient:
    return TestClient(create_app(mock_health=True, enable_precheck_loop=False), client=("127.0.0.1", 5555))


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "OPENVAULT_GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(github_auth, "_token_from_gh", lambda: None)
    github_auth.bind_vault(None)
    yield tmp_path
    github_auth.bind_vault(None)


def _plaintext_hits(home: Path, needle: str) -> list[Path]:
    hits: list[Path] = []
    for path in home.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in {".db", ".sqlite"}:
            # Ciphertext may coincidentally contain ascii; only check text sidecars.
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if needle in text:
            hits.append(path)
    return hits


def test_save_pat_no_plaintext_on_disk(tmp_path: Path) -> None:
    vault = KeyVault()
    github_auth.bind_vault(vault)
    save_pat(TOKEN, note="ship", vault=vault)

    pat_path = tmp_path / "github" / "pat.json"
    assert not pat_path.is_file()
    assert _plaintext_hits(tmp_path, TOKEN) == []

    got, mode = resolve_token(vault=vault)
    assert mode == "pat"
    assert got == TOKEN
    assert vault.get(GITHUB_SHIP_PAT_ID) is not None
    assert vault.get(GITHUB_SHIP_PAT_ID).enabled is False


def test_migrate_legacy_pat_json_then_scrub(tmp_path: Path) -> None:
    vault = KeyVault()
    pat_path = tmp_path / "github" / "pat.json"
    pat_path.parent.mkdir(parents=True)
    pat_path.write_text(
        json.dumps({"token": TOKEN, "note": "legacy"}),
        encoding="utf-8",
    )

    got, mode = resolve_token(vault=vault)
    assert mode == "pat"
    assert got == TOKEN
    assert not pat_path.is_file()
    assert vault.get_secret(GITHUB_SHIP_PAT_ID) == TOKEN
    assert _plaintext_hits(tmp_path, TOKEN) == []


def test_clear_pat_removes_sealed_record(tmp_path: Path) -> None:
    vault = KeyVault()
    save_pat(TOKEN, vault=vault)
    clear_pat(vault=vault)
    assert vault.get(GITHUB_SHIP_PAT_ID) is None
    got, mode = resolve_token(vault=vault)
    assert got is None
    assert mode == "disconnected"


def test_save_and_clear_fail_closed_when_sealed(tmp_path: Path) -> None:
    seal = Seal()
    vault = KeyVault(seal=seal)
    save_pat(TOKEN, vault=vault)
    seal.set_passphrase(PASSPHRASE)
    seal.lock()
    assert seal.is_sealed

    with pytest.raises(VaultSealedError):
        save_pat("ghp_other", vault=vault)
    with pytest.raises(VaultSealedError):
        clear_pat(vault=vault)

    got, mode = resolve_token(vault=vault)
    assert got is None
    assert mode == "disconnected"

    seal.unseal(PASSPHRASE)
    got, mode = resolve_token(vault=vault)
    assert mode == "pat"
    assert got == TOKEN


def test_api_pat_save_resolve_clear_and_sealed_gate(tmp_path: Path) -> None:
    client = _client()
    saved = client.post("/api/ship/github/pat", json={"token": TOKEN, "note": "api"})
    assert saved.status_code == 200, saved.text
    assert not (tmp_path / "github" / "pat.json").is_file()
    assert _plaintext_hits(tmp_path, TOKEN) == []

    # Prefer vault PAT when gh is stubbed out.
    status = client.get("/api/ship/github/status")
    assert status.status_code == 200
    # connection_status hits GitHub API; without network it may be disconnected
    # but vault row must exist.
    vault = KeyVault()
    assert vault.get_secret(GITHUB_SHIP_PAT_ID) == TOKEN

    set_pp = client.post("/api/vault/passphrase", json={"passphrase": PASSPHRASE})
    assert set_pp.status_code == 200, set_pp.text

    # Restart → sealed
    client2 = _client()
    assert client2.get("/api/vault/status").json()["sealed"] is True
    refused = client2.post("/api/ship/github/pat", json={"token": "ghp_nope"})
    assert refused.status_code == 403
    assert "sealed" in refused.json()["detail"].lower()

    cleared_refused = client2.delete("/api/ship/github/pat")
    assert cleared_refused.status_code == 403

    unseal = client2.post("/api/vault/unseal", json={"passphrase": PASSPHRASE})
    assert unseal.status_code == 200
    vault2 = KeyVault(seal=Seal())
    # New Seal after unseal on app's Seal — bind via API clear on same app seal:
    ok_clear = client2.delete("/api/ship/github/pat")
    assert ok_clear.status_code == 200, ok_clear.text

    # App's vault deleted the row; a fresh KeyVault on same db sees absence after
    # the app Seal is open — use app-bound path: resolve through status alone.
    # Re-open a KeyVault sharing home; DPAPI/passphrase file is passphrase-wrapped
    # so a brand-new Seal starts sealed. Unseal locally to inspect.
    inspect = Seal()
    assert inspect.is_sealed
    inspect.unseal(PASSPHRASE)
    assert KeyVault(seal=inspect).get(GITHUB_SHIP_PAT_ID) is None


def test_legacy_pat_not_used_while_sealed(tmp_path: Path) -> None:
    seal = Seal()
    vault = KeyVault(seal=seal)
    seal.set_passphrase(PASSPHRASE)
    seal.lock()

    pat_path = tmp_path / "github" / "pat.json"
    pat_path.parent.mkdir(parents=True)
    pat_path.write_text(json.dumps({"token": TOKEN}), encoding="utf-8")

    got, mode = resolve_token(vault=vault)
    assert got is None
    assert mode == "disconnected"
    assert pat_path.is_file()  # migrate deferred until unseal

    seal.unseal(PASSPHRASE)
    got, mode = resolve_token(vault=vault)
    assert got == TOKEN
    assert mode == "pat"
    assert not pat_path.is_file()
