"""#50: public JWKS kids + register deep-links. Mint is not world-open.

Proved against the live host before #50:
``/.well-known/jwks.json`` 404, ``/keys/jwks`` ``keys=[]``.
#52 allowlists prove VPC peers for ``POST /keys/services`` only; unlisted
remotes stay 403. Intermediate issue stays loopback-only. No public ``:5000``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.system_plane import INTERNAL_WRITERS_URL, bind_policy
from openmw.openvault.vault.trust import TrustStore

INTENT = {"X-OpenVault-Reveal": "intentional"}


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.delenv("LIVE_KEY_ID", raising=False)
    monkeypatch.delenv("OPENVAULT_LIVE_KEY_ID", raising=False)
    return root


def _client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(
        create_app(mock_health=True, enable_precheck_loop=False, cortex_url="http://127.0.0.1:9"),
        client=(host, 5555),
    )


def test_well_known_jwks_has_kids_without_mint(home: Path) -> None:
    client = _client()
    well = client.get("/.well-known/jwks.json")
    alt = client.get("/keys/jwks")
    assert well.status_code == 200
    assert alt.status_code == 200
    assert well.json() == alt.json()
    keys = well.json()["keys"]
    assert keys, "Cortex bind needs at least one kid"
    kids = [k["kid"] for k in keys]
    assert all(kids)
    assert any(k.startswith("root-") for k in kids)
    blob = well.text.lower()
    assert "ov_" not in blob
    assert "private_key" not in blob
    assert well.headers.get("access-control-allow-origin") == "*"


def test_remote_caller_can_read_jwks_but_cannot_mint(home: Path) -> None:
    remote = _client(host="10.0.0.9")
    jwks = remote.get("/.well-known/jwks.json")
    assert jwks.status_code == 200
    assert jwks.json()["keys"]
    mint = remote.post("/keys/services", json={"service_id": "dms"}, headers=INTENT)
    assert mint.status_code == 403
    assert "OPENVAULT_SERVICES_ALLOW" in mint.json()["detail"]
    assert remote.post("/keys/intermediate", json={"service_id": "dms"}).status_code == 403
    assert remote.post("/api/apikeys", json={"label": "nope", "tier": "free"}).status_code == 403


def test_healthz_advertises_jwks_uri(home: Path) -> None:
    body = _client().get("/api/healthz").json()
    assert body["jwks_uri"] == "/.well-known/jwks.json"
    assert body["service"] == "openvault"


def test_api_keys_empty_is_not_the_jwks(home: Path) -> None:
    """Platform saw /api/keys keys=[] and treated it as missing JWKS. Different store."""
    client = _client()
    vault_keys = client.get("/api/keys").json()
    assert vault_keys.get("keys") == []
    jwks = client.get("/.well-known/jwks.json").json()
    assert jwks["keys"], "JWKS kids must exist even when the vault list is empty"


def test_bind_policy_points_at_well_known_jwks() -> None:
    policy = bind_policy()
    assert policy["jwks_uri"] == "/.well-known/jwks.json"
    assert policy["jwks_alt"] == "/keys/jwks"
    assert policy["mint_loopback_only"] is True
    assert policy["services_allow_env"] == "OPENVAULT_SERVICES_ALLOW"
    assert policy["jwks_url"] == f"{INTERNAL_WRITERS_URL}/.well-known/jwks.json"
    assert "ov_" not in str(policy)


def test_live_key_id_env_is_echoed_only_when_not_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVE_KEY_ID", "119691f2c637")
    assert bind_policy()["live_key_id"] == "119691f2c637"
    monkeypatch.setenv("LIVE_KEY_ID", "ov_this_must_never_be_echoed")
    assert bind_policy()["live_key_id"] is None


def test_tool_register_deep_link_uses_catalog_url(home: Path) -> None:
    client = _client()
    catalog = client.get("/api/tool/register")
    assert catalog.status_code == 200
    ids = {row["provider"] for row in catalog.json()["providers"]}
    assert "groq" in ids
    groq = client.get("/api/tool/register", params={"provider": "groq"})
    assert groq.status_code == 200
    body = groq.json()
    assert body["register_url"].startswith("https://")
    assert body["deep_link"] == "/tool/register?provider=groq"
    assert "rates" not in body["deep_link"]
    assert client.get("/api/tool/register", params={"provider": "nope"}).status_code == 404


def test_freeroute_status_reports_kids_and_spendable(home: Path) -> None:
    client = _client()
    body = client.get("/api/freeroute/status").json()
    assert body["ok"] is True
    assert body["kid_count"] >= 1
    assert body["jwks_uri"] == "/.well-known/jwks.json"
    assert body["usage_unit_status"] == "NEEDS-YOU"
    spendable_ids = {row["id"] for row in body["spendable"]}
    assert "together" in spendable_ids
    assert "siliconflow" in spendable_ids
    assert "github_models" not in spendable_ids


def test_jwks_pin_kid_matches_root_document(home: Path) -> None:
    store = TrustStore(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))
    root = store.ensure_root()
    pin = next(k for k in store.jwks()["keys"] if str(k["kid"]).startswith("root-"))
    assert pin["kid"] == root.kid
    assert pin["x"] == root.public_key


def test_jwks_still_publishes_existing_root_when_sealed(home: Path) -> None:
    """Prove host already had a root; Cortex must pin it even if the vault is locked."""
    seal = Seal(Fernet.generate_key())
    store = TrustStore(db_path=home / "keys.db", seal=seal)
    root = store.ensure_root()
    seal.lock()
    keys = store.jwks()["keys"]
    kids = [k["kid"] for k in keys]
    assert root.kid in kids
    assert all("d" not in k and "private_key" not in k for k in keys)
