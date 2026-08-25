"""Whose key does a metered caller spend? (#36, option (a))

The gateway spends OpenVault's own pooled keys. A key marked ``tenant`` is
stored, but it never enters the fallback pool, so no metered caller can reach
it however healthy or high-priority it looks.

Every assertion here is made at the layer the customer receives (R-0001): the
HTTP response and the JSON of ``GET /api/usage``. Asserting on
``FallbackManager.ordered_candidates`` directly would pass just as happily if
the route wiring in between were broken, and the wiring is the part that was
missing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.store import KeyRecord, KeyVault

_CHAT: dict[str, Any] = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
_USAGE = {"total_tokens": 40, "prompt_tokens": 20, "completion_tokens": 20}


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KeyVault:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENVAULT_KEY", Fernet.generate_key().decode())
    return KeyVault(db_path=tmp_path / "keys.db", seal=Seal())


@pytest.fixture
def client(vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def _ok_response(payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "{}"
    resp.headers = {}
    resp.json = MagicMock(return_value=payload)
    return resp


def _mock_client(*responses: MagicMock) -> MagicMock:
    mock = MagicMock()
    if len(responses) == 1:
        mock.post = AsyncMock(return_value=responses[0])
    else:
        mock.post = AsyncMock(side_effect=list(responses))
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


def _key(vault: KeyVault, label: str, *, custody: str = "pooled", priority: int = 100) -> KeyRecord:
    """A healthy key the walk would happily pick if custody allowed it."""
    rec = vault.create(
        label=label,
        provider="openai",
        secret=f"sk-test-{label}-aaaaaaaa",
        role="primary",
        priority=priority,
        base_url="https://example.invalid/v1",
        custody=custody,
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)
    return rec


class TestTenantKeysNeverServeAMeteredCaller:
    def test_tenant_key_is_not_spent_even_though_it_would_have_won_the_walk(
        self, client: TestClient, vault: KeyVault
    ) -> None:
        """The exact hole #36 named, asserted on the ledger row.

        The tenant key is healthy and sits at priority 0, so on the old walk it
        would have been selected first. Custody has to beat priority, or the
        operator's own ordering silently decides whose money gets spent.
        """
        tenant_b = _key(vault, "tenant-b-key", custody="tenant", priority=0)
        ours = _key(vault, "our-pooled-key", custody="pooled", priority=100)

        _tenant_a, headers = issue_key(client, label="tenant-a")
        mock = _mock_client(_ok_response({"id": "r", "choices": [], "usage": _USAGE}))
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            resp = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        assert resp.status_code == 200

        events = client.get("/api/usage").json()["events"]
        assert len(events) == 1
        assert events[0]["vault_key_id"] == ours.id
        assert events[0]["vault_key_id"] != tenant_b.id, "tenant A spent a key tenant B uploaded"

    def test_a_vault_holding_only_tenant_keys_refuses_rather_than_falling_back(
        self, client: TestClient, vault: KeyVault
    ) -> None:
        """No pooled key must be a typed refusal, not a silent walk into theirs."""
        tenant_b = _key(vault, "tenant-b-only", custody="tenant", priority=0)

        _tenant_a, headers = issue_key(client, label="tenant-a")
        resp = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "openvault_no_pooled_keys"

        row = client.get("/api/usage").json()["events"][0]
        assert row["status"] == 503
        assert row["vault_key_id"] != tenant_b.id
        assert row["billable_tokens"] == 0

    def test_refusal_says_which_kind_of_empty_pool_this_is(
        self, client: TestClient, vault: KeyVault
    ) -> None:
        """An empty vault and a tenant-only vault are different problems (R-0011).

        Telling an operator "no healthy API keys" while the vault visibly holds
        keys sends them looking in the wrong place.
        """
        _tenant_a, headers = issue_key(client, label="tenant-a")

        empty = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        assert empty.json()["error"]["type"] == "openvault_no_keys"

        _key(vault, "theirs", custody="tenant")
        held = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        assert held.json()["error"]["type"] == "openvault_no_pooled_keys"
        assert "tenant-custody" in held.json()["error"]["message"]


class TestPoolMembership:
    def test_a_key_created_without_a_custody_argument_is_ours(self, client: TestClient) -> None:
        """Default pooled: every key predating the tag was the operator's own."""
        resp = client.post(
            "/api/keys",
            json={"label": "no-custody-given", "provider": "openai", "secret": "sk-x-aaaaaaaa"},
        )
        assert resp.status_code == 200
        assert resp.json()["custody"] == "pooled"

    def test_the_api_can_mark_a_key_tenant_custody(self, client: TestClient) -> None:
        resp = client.post(
            "/api/keys",
            json={
                "label": "theirs",
                "provider": "openai",
                "secret": "sk-y-aaaaaaaa",
                "custody": "tenant",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["custody"] == "tenant"

    def test_upgrading_an_existing_vault_does_not_empty_the_pool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The migration backfills 'pooled'.

        Defaulting the other way would 503 every route the moment somebody
        upgraded, which is a worse outage than the bug being fixed.
        """
        monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("OPENVAULT_KEY", Fernet.generate_key().decode())
        db = tmp_path / "legacy.db"

        vault = KeyVault(db_path=db, seal=Seal())
        _key(vault, "pre-existing")

        # Reproduce a vault written before this column existed.
        with sqlite3.connect(str(db)) as conn:
            conn.execute("ALTER TABLE keys DROP COLUMN custody")
            conn.commit()

        reopened = KeyVault(db_path=db, seal=Seal())
        pooled = reopened.pooled_ordered()
        assert len(pooled) == 1
        assert pooled[0].custody == "pooled"

    def test_the_walk_refuses_a_tenant_key_even_if_handed_one_directly(
        self, vault: KeyVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pin the second layer, which the end-to-end tests cannot reach.

        ``ordered_candidates`` sources from ``pooled_ordered``, so as long as
        that filter holds, the availability guard in ``_is_available`` is never
        exercised and could be deleted without a single test noticing. This
        reproduces the regression it exists for: a future caller wires the walk
        back to the unfiltered list. Custody must still refuse.
        """
        ours = _key(vault, "ours", custody="pooled")
        tenant = _key(vault, "theirs", custody="tenant", priority=0)

        # The exact mistake: pool membership sourced from every enabled key.
        monkeypatch.setattr(vault, "pooled_ordered", vault.enabled_ordered)

        manager = FallbackManager(vault, config_path=tmp_path / "fallback.json")
        picked = [r.id for r in manager.ordered_candidates()]
        assert tenant.id not in picked, "custody guard did not hold at the walk"
        assert picked == [ours.id]

    def test_fallback_status_does_not_advertise_a_key_it_can_never_use(
        self, vault: KeyVault, tmp_path: Path
    ) -> None:
        """The hop dashboard has to match the walk, or it lies about capacity."""
        _key(vault, "ours", custody="pooled")
        tenant = _key(vault, "theirs", custody="tenant")

        manager = FallbackManager(vault, config_path=tmp_path / "fallback.json")
        shown = {hop["key_id"] for hop in manager.status().hops}
        assert tenant.id not in shown
        assert len(shown) == 1


class TestAccountAttachedKeysAreTenant:
    def test_account_attached_key_is_tenant_and_not_in_pooled_ordered(
        self, client: TestClient, vault: KeyVault
    ) -> None:
        acct = client.post("/api/accounts", json={"display_name": "Tenant B"}).json()
        attached = client.post(
            f"/api/accounts/{acct['id']}/keys",
            json={
                "label": "their-byok",
                "provider": "openai",
                "secret": "sk-tenant-aaaaaaaa",
            },
        )
        assert attached.status_code == 200, attached.text
        body = attached.json()
        assert body["custody"] == "tenant"
        pooled_ids = {row.id for row in vault.pooled_ordered()}
        assert body["id"] not in pooled_ids

    def test_account_attached_key_loses_the_walk_to_a_lower_priority_pooled_key(
        self, client: TestClient, vault: KeyVault
    ) -> None:
        ours = _key(vault, "our-pooled-key", custody="pooled", priority=100)
        acct = client.post("/api/accounts", json={"display_name": "Tenant B"}).json()
        attached = client.post(
            f"/api/accounts/{acct['id']}/keys",
            json={
                "label": "their-byok",
                "provider": "openai",
                "secret": "sk-tenant-bbbbbbbb",
                "priority": 0,
            },
        )
        assert attached.status_code == 200
        tenant_id = attached.json()["id"]
        rec = vault.get(tenant_id)
        assert rec is not None
        vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)

        _tenant_a, headers = issue_key(client, label="tenant-a")
        mock = _mock_client(_ok_response({"id": "r", "choices": [], "usage": _USAGE}))
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            resp = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        assert resp.status_code == 200
        assert client.get("/api/usage").json()["events"][0]["vault_key_id"] == ours.id

    def test_patch_keys_cannot_move_a_tenant_key_into_the_pool(self, client: TestClient) -> None:
        acct = client.post("/api/accounts", json={"display_name": "Tenant B"}).json()
        attached = client.post(
            f"/api/accounts/{acct['id']}/keys",
            json={
                "label": "their-byok",
                "provider": "openai",
                "secret": "sk-tenant-cccccccc",
            },
        ).json()
        patched = client.patch(
            f"/api/keys/{attached['id']}",
            json={"custody": "pooled", "label": "still-theirs"},
        )
        assert patched.status_code == 200
        assert patched.json()["custody"] == "tenant"
