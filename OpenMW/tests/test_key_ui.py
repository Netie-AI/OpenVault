"""Friendly key UI lock: subscribe copy, Cortex ov_ issue, tenant BYOK custody.

Proof path does not import create_app (main still pulls missing PR #9 ship
modules). The key-UI router is mounted on a tiny FastAPI app instead.
No public :5000 bind.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.routers.key_ui import build_key_ui_router
from openmw.openvault.vault.accounts import AccountStore
from openmw.openvault.vault.cortex_key import (
    issue_cortex_key,
    issued_payload,
    tenant_key_payload,
)
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.key_ui_copy import (
    CORTEX_KEY_LABEL,
    FORBIDDEN_SUBSCRIBE_TERMS,
    POWERED_BY,
    subscribe_surface,
)
from openmw.openvault.vault.store import KeyVault

REPO_ROOT = Path(__file__).resolve().parents[2]
WEBUI = REPO_ROOT / "OpenMW" / "webui" / "index.html"
TS_COPY = REPO_ROOT / "apps" / "web" / "src" / "keys" / "copy.ts"
TS_RENDER = REPO_ROOT / "apps" / "web" / "src" / "keys" / "render.ts"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    return root


@pytest.fixture()
def vault(home: Path) -> KeyVault:
    return KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))


@pytest.fixture()
def accounts(home: Path) -> AccountStore:
    return AccountStore(db_path=home / "accounts.db")


@pytest.fixture()
def client(vault: KeyVault, accounts: AccountStore) -> TestClient:
    app = FastAPI()
    app.include_router(build_key_ui_router(vault, accounts))
    return TestClient(app)


def _subscribe_html() -> str:
    html = WEBUI.read_text(encoding="utf-8")
    start = html.index('id="keypath-subscribe"')
    end = html.index('id="keypath-byok"')
    return html[start:end]


def test_subscribe_copy_lock() -> None:
    surface = subscribe_surface()
    assert "Cortex API key" in surface
    assert "Safety:" in surface
    assert POWERED_BY in surface
    for term in FORBIDDEN_SUBSCRIBE_TERMS:
        assert term not in surface


def test_ts_and_webui_subscribe_copy_match_the_lock() -> None:
    ts = TS_COPY.read_text(encoding="utf-8")
    ts_subscribe = ts.split("export const FORBIDDEN_SUBSCRIBE_TERMS")[0]
    render = TS_RENDER.read_text(encoding="utf-8")
    webui_sub = _subscribe_html()
    for term in FORBIDDEN_SUBSCRIBE_TERMS:
        assert term not in ts_subscribe
        assert term not in render
        assert term not in webui_sub
    assert "Cortex API key" in webui_sub
    assert POWERED_BY in webui_sub
    assert "Safety:" in webui_sub
    assert CORTEX_KEY_LABEL in ts


def test_ui_copy_api(client: TestClient) -> None:
    res = client.get("/api/keys/ui-copy")
    assert res.status_code == 200
    body = res.json()
    assert body["subscribe"]["issued_label"] == CORTEX_KEY_LABEL
    assert POWERED_BY in body["subscribe"]["disclosure"]
    joined = " ".join(str(v) for v in body["subscribe"].values())
    for term in FORBIDDEN_SUBSCRIBE_TERMS:
        assert term not in joined


def test_issue_cortex_key_is_ov_framed_as_cortex(
    client: TestClient, vault: KeyVault
) -> None:
    res = client.post("/api/keys/cortex")
    assert res.status_code == 200
    body = res.json()
    assert body["display_label"] == CORTEX_KEY_LABEL
    assert body["provider"] == "cortex"
    assert body["token"].startswith("ov_")
    assert body["pooled"] is False
    assert body["custody"] == "operator"
    listed = vault.list_keys()
    assert listed[0].provider == "cortex"
    assert listed[0].label == CORTEX_KEY_LABEL


def test_account_cortex_key_is_tenant_not_pooled(
    client: TestClient, accounts: AccountStore
) -> None:
    acct = accounts.create(
        display_name="Seat", auth_provider="netie_email", local_part="seat"
    )
    res = client.post(f"/api/accounts/{acct.id}/cortex-key")
    assert res.status_code == 200
    body = res.json()
    assert body["account_id"] == acct.id
    assert body["custody"] == "tenant"
    assert body["pooled"] is False
    assert body["token"].startswith("ov_")
    assert body["display_label"] == CORTEX_KEY_LABEL


def test_account_byok_is_not_silently_pooled(vault: KeyVault, accounts: AccountStore) -> None:
    acct = accounts.create(
        display_name="BYOK", auth_provider="netie_email", local_part="byok"
    )
    record = vault.create(
        label="Groq lab",
        provider="groq",
        secret="gsk-user-brought-this",
        role="backup",
        account_id=acct.id,
    )
    body = tenant_key_payload(record)
    assert body["account_id"] == acct.id
    assert body["provider"] == "groq"
    assert body["custody"] == "tenant"
    assert body["pooled"] is False
    assert getattr(record, "pooled", False) is False


def test_issue_helper_matches_api_shape(vault: KeyVault) -> None:
    record, token = issue_cortex_key(vault)
    payload = issued_payload(record, token)
    assert payload["token"].startswith("ov_")
    assert payload["display_label"] == CORTEX_KEY_LABEL
    assert payload["pooled"] is False
