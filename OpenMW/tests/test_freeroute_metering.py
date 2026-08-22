"""Issued keys, the usage ledger, the output ceiling, and hop affinity.

Every assertion here is made at the layer the customer receives (R-0001): the
HTTP response, the JSON of GET /api/usage, or the body the fake upstream
actually received. Asserting on the limiter object or the store would pass just
as happily if the wiring in between were broken.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.api_keys import ApiKeyError, ApiKeyStore
from openmw.openvault.vault.auth import AuthRefusedError, Caller, is_loopback, resolve_caller
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.proxy import affinity_key_for, chat_completions
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.usage_store import HopTrace

_CHAT: dict[str, Any] = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}


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
        # side_effect with a single MagicMock would *call* it, not return it.
        mock.post = AsyncMock(return_value=responses[0])
    else:
        mock.post = AsyncMock(side_effect=list(responses))
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


def _healthy_key(vault: KeyVault, label: str, *, provider: str = "openai", priority: int = 0):
    rec = vault.create(
        label=label,
        provider=provider,
        secret=f"sk-test-{label}-aaaaaaaa",
        role="primary",
        priority=priority,
        base_url="https://example.invalid/v1",
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)
    return rec


# ─── Issued keys replace the header-asserted identity ────────────────────────


class TestIssuedKeys:
    def test_tier_header_no_longer_buys_the_unmetered_tier(self, client: TestClient) -> None:
        """The whole point: a caller cannot select its own tier any more.

        `local` is 6000 rpm / 6M tpm. Before, one header bought it. Now a free-tier
        key stays throttled at 20/min even while shouting the old header.
        """
        _identity, headers = issue_key(client, tier="free")
        spoof = {**headers, "x-openfree-tier": "local", "x-openfree-identity": "somebody-else"}
        saw_429 = False
        for _ in range(25):
            resp = client.post("/v1/chat/completions", json=_CHAT, headers=spoof)
            if resp.status_code == 429:
                saw_429 = True
                assert resp.headers.get("X-RateLimit-Tier") == "free"
                break
            assert resp.status_code in (502, 503)
        assert saw_429, "the free tier must still throttle a caller claiming to be local"

    def test_unknown_key_is_401_and_spends_nothing(self, client: TestClient) -> None:
        mock = _mock_client(_ok_response({"id": "never"}))
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            resp = client.post(
                "/v1/chat/completions",
                json=_CHAT,
                headers={"Authorization": "Bearer ov_not-a-real-key"},
            )
        assert resp.status_code == 401
        assert resp.json()["error"]["type"] == "openvault_unauthenticated"
        assert mock.post.await_count == 0, "an unauthenticated call must not reach a provider"

    def test_revoked_key_stops_working_without_a_restart(self, client: TestClient) -> None:
        key_id, headers = issue_key(client)
        first = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        assert first.status_code != 401

        revoked = client.delete(f"/api/apikeys/{key_id}")
        assert revoked.status_code == 200

        after = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        assert after.status_code == 401
        assert after.json()["error"]["type"] == "openvault_unauthenticated"

    def test_revoked_and_unknown_are_indistinguishable(self, client: TestClient) -> None:
        """A caller must not learn that a key once existed."""
        key_id, headers = issue_key(client)
        client.delete(f"/api/apikeys/{key_id}")
        revoked = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        unknown = client.post(
            "/v1/chat/completions", json=_CHAT, headers={"Authorization": "Bearer ov_nope"}
        )
        assert revoked.status_code == unknown.status_code == 401
        assert revoked.json() == unknown.json()

    def test_token_is_shown_once_and_never_stored(self, client: TestClient, tmp_path: Path) -> None:
        out = client.post("/api/apikeys", json={"label": "once", "tier": "free"}).json()
        token = out["token"]
        listed = client.get("/api/apikeys").json()["keys"]
        assert all(token not in str(k) for k in listed), "the raw token must never be listed"
        assert out["key"]["display"].startswith("ov_")
        assert len(out["key"]["display"]) < len(token)

    def test_local_tier_cannot_be_issued(self, client: TestClient) -> None:
        """Issuing `local` would hand out the exact bypass this closes."""
        resp = client.post("/api/apikeys", json={"label": "sneaky", "tier": "local"})
        assert resp.status_code == 400
        assert "unmetered" in resp.json()["detail"]

    def test_store_refuses_unnamed_keys(self, tmp_path: Path) -> None:
        store = ApiKeyStore(db_path=tmp_path / "keys.db")
        with pytest.raises(ApiKeyError, match="label is required"):
            store.issue(label="   ", tier="free")


class TestCallerResolution:
    class _Req:
        def __init__(self, host: str, headers: dict[str, str] | None = None) -> None:
            self.client = type("C", (), {"host": host})()
            self.headers = headers or {}

    def test_remote_caller_without_a_key_is_refused(self, tmp_path: Path) -> None:
        store = ApiKeyStore(db_path=tmp_path / "keys.db")
        with pytest.raises(AuthRefusedError) as exc:
            resolve_caller(self._Req("203.0.113.10"), api_keys=store)
        assert exc.value.status_code == 401

    def test_loopback_keeps_working_for_local_development(self, tmp_path: Path) -> None:
        """A control that refuses legitimate work is a failure (R-0005)."""
        store = ApiKeyStore(db_path=tmp_path / "keys.db")
        caller = resolve_caller(self._Req("127.0.0.1"), api_keys=store)
        assert isinstance(caller, Caller)
        assert caller.tier == "local"
        assert caller.api_key_id is None

    def test_require_api_key_closes_the_loopback_exemption(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENVAULT_REQUIRE_API_KEY", "1")
        store = ApiKeyStore(db_path=tmp_path / "keys.db")
        with pytest.raises(AuthRefusedError):
            resolve_caller(self._Req("127.0.0.1"), api_keys=store)

    def test_forwarded_headers_cannot_fake_loopback(self, tmp_path: Path) -> None:
        store = ApiKeyStore(db_path=tmp_path / "keys.db")
        spoofed = self._Req(
            "203.0.113.10", {"x-forwarded-for": "127.0.0.1", "x-real-ip": "127.0.0.1"}
        )
        with pytest.raises(AuthRefusedError):
            resolve_caller(spoofed, api_keys=store)

    @pytest.mark.parametrize(
        ("host", "expected"),
        [("127.0.0.1", True), ("::1", True), ("localhost", True), ("10.0.0.4", False), ("", False)],
    )
    def test_is_loopback(self, host: str, expected: bool) -> None:
        assert is_loopback(host) is expected


# ─── Usage ledger ────────────────────────────────────────────────────────────


class TestUsageLedger:
    def test_row_names_the_hop_that_actually_served(
        self, client: TestClient, vault: KeyVault
    ) -> None:
        """The caller asked for `auto`; the ledger must record what really ran."""
        _healthy_key(vault, "groq-hop", provider="groq")
        key_id, headers = issue_key(client)
        payload = {
            "id": "chatcmpl-1",
            "choices": [],
            "usage": {"prompt_tokens": 11, "completion_tokens": 9, "total_tokens": 20},
        }
        mock = _mock_client(_ok_response(payload))
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            resp = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        assert resp.status_code == 200

        usage = client.get("/api/usage", params={"api_key_id": key_id}).json()
        assert usage["count"] == 1
        row = usage["events"][0]
        assert row["model_requested"] == "auto"
        assert row["provider"] == "groq"
        assert row["model_served"] and row["model_served"] != "auto"
        assert row["total_tokens"] == 20
        assert row["estimated"] is False
        assert row["api_key_id"] == key_id

    def test_fallback_records_one_row_naming_the_key_that_won(
        self, client: TestClient, vault: KeyVault
    ) -> None:
        first = _healthy_key(vault, "first-hop", priority=0)
        second = _healthy_key(vault, "second-hop", priority=1)
        _key_id, headers = issue_key(client)

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.text = "rate limit"
        resp_429.headers = {}
        resp_429.json = MagicMock(side_effect=ValueError("not json"))
        ok = _ok_response(
            {
                "id": "x",
                "choices": [],
                "usage": {"total_tokens": 7, "prompt_tokens": 3, "completion_tokens": 4},
            }
        )
        mock = _mock_client(resp_429, ok)
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            resp = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        assert resp.status_code == 200

        events = client.get("/api/usage").json()["events"]
        assert len(events) == 1, "one request is one ledger row, however many hops it took"
        assert events[0]["vault_key_id"] == second.id
        assert events[0]["vault_key_id"] != first.id

    def test_failed_request_is_recorded_with_its_refusal_type(self, client: TestClient) -> None:
        _key_id, headers = issue_key(client)
        resp = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        assert resp.status_code == 503

        row = client.get("/api/usage").json()["events"][0]
        assert row["status"] == 503
        assert row["total_tokens"] == 0
        assert row["billable_tokens"] == 0

    def test_summary_separates_estimated_from_measured(
        self, client: TestClient, vault: KeyVault
    ) -> None:
        """A total that silently mixes guessed and measured tokens is unbillable."""
        _healthy_key(vault, "sum-hop")
        _key_id, headers = issue_key(client)
        ok = _ok_response(
            {
                "id": "s",
                "choices": [],
                "usage": {"total_tokens": 40, "prompt_tokens": 20, "completion_tokens": 20},
            }
        )
        mock = _mock_client(ok)
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            client.post("/v1/chat/completions", json=_CHAT, headers=headers)

        summary = client.get("/api/usage").json()["summary"]
        assert summary["total_tokens"] == 40
        assert summary["estimated_tokens"] == 0
        assert summary["billable_tokens"] == 40
        assert summary["priced"] is False, "OpenVault must not invent a price"


# ─── Output budget ceiling and context refusal ───────────────────────────────


class TestOutputBudget:
    def _posted_body(self, mock: MagicMock) -> dict[str, Any]:
        assert mock.post.await_count >= 1
        return mock.post.await_args.kwargs["json"]

    def test_invalid_max_tokens_is_dropped_not_forwarded(
        self, client: TestClient, vault: KeyVault
    ) -> None:
        """`max_tokens: 0` earns a 400 from every provider, so it walked the pool."""
        _healthy_key(vault, "budget-hop")
        _key_id, headers = issue_key(client)
        mock = _mock_client(_ok_response({"id": "b", "choices": []}))
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            resp = client.post(
                "/v1/chat/completions", json={**_CHAT, "max_tokens": 0}, headers=headers
            )
        assert resp.status_code == 200
        assert "max_tokens" not in self._posted_body(mock)

    def test_operator_ceiling_clamps_the_body_that_goes_upstream(
        self, client: TestClient, vault: KeyVault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENVAULT_MAX_OUTPUT_TOKENS", "256")
        _healthy_key(vault, "clamp-hop")
        _key_id, headers = issue_key(client)
        mock = _mock_client(_ok_response({"id": "c", "choices": []}))
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            resp = client.post(
                "/v1/chat/completions", json={**_CHAT, "max_tokens": 1_000_000}, headers=headers
            )
        assert resp.status_code == 200, (
            "the ceiling must apply before the budget reservation, or a caller is "
            "429'd out of their own quota for tokens we would never send"
        )
        assert self._posted_body(mock)["max_tokens"] == 256

    def test_no_ceiling_configured_leaves_a_large_budget_alone(
        self, client: TestClient, vault: KeyVault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R-0005: a ceiling nobody asked for would refuse legitimate long output."""
        monkeypatch.delenv("OPENVAULT_MAX_OUTPUT_TOKENS", raising=False)
        monkeypatch.delenv("OPENVAULT_CONTEXT_WINDOWS", raising=False)
        _healthy_key(vault, "free-budget-hop")
        _key_id, headers = issue_key(client)
        mock = _mock_client(_ok_response({"id": "d", "choices": []}))
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            client.post(
                "/v1/chat/completions", json={**_CHAT, "max_tokens": 32_000}, headers=headers
            )
        assert self._posted_body(mock)["max_tokens"] == 32_000

    def test_oversized_prompt_refuses_before_spending_a_single_call(
        self, client: TestClient, vault: KeyVault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENVAULT_CONTEXT_WINDOWS", '{"openai": 1000}')
        _healthy_key(vault, "ctx-hop-a", priority=0)
        _healthy_key(vault, "ctx-hop-b", priority=1)
        _key_id, headers = issue_key(client)
        huge = {"model": "auto", "messages": [{"role": "user", "content": "x" * 80_000}]}
        mock = _mock_client(_ok_response({"id": "never", "choices": []}))
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            resp = client.post("/v1/chat/completions", json=huge, headers=headers)
        assert resp.status_code == 400
        assert resp.json()["error"]["type"] == "openvault_context_length_exceeded"
        assert mock.post.await_count == 0, (
            "a prompt nobody can serve must not burn one upstream call per key"
        )

    def test_prompt_that_fits_still_goes_through(
        self, client: TestClient, vault: KeyVault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENVAULT_CONTEXT_WINDOWS", '{"openai": 100000}')
        _healthy_key(vault, "ctx-ok-hop")
        _key_id, headers = issue_key(client)
        mock = _mock_client(_ok_response({"id": "fits", "choices": []}))
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            resp = client.post("/v1/chat/completions", json=_CHAT, headers=headers)
        assert resp.status_code == 200
        assert mock.post.await_count == 1


# ─── Prompt-cache affinity ───────────────────────────────────────────────────


class TestAffinity:
    def test_single_turn_requests_are_not_pinned(self) -> None:
        """Pinning one-shot traffic trades a cache we would miss for a limit we would hit."""
        assert affinity_key_for({"messages": [{"role": "user", "content": "hi"}]}) == ""

    def test_same_prefix_same_key_different_prefix_may_differ(self) -> None:
        base = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "turn one"},
            {"role": "assistant", "content": "answer one"},
        ]
        a = affinity_key_for({"messages": [*base, {"role": "user", "content": "turn two"}]})
        b = affinity_key_for({"messages": [*base, {"role": "user", "content": "different"}]})
        assert a and a == b, "the reusable prefix is the same, so the hop must be the same"

        other = affinity_key_for(
            {
                "messages": [
                    {"role": "system", "content": "you are terse"},
                    {"role": "user", "content": "turn one"},
                    {"role": "assistant", "content": "answer one"},
                    {"role": "user", "content": "turn two"},
                ]
            }
        )
        assert other != a

    def test_explicit_cache_key_wins_but_is_namespaced_per_tenant(self) -> None:
        """Two tenants both sending "default" must not land on one vault key."""
        body = {"prompt_cache_key": "default", "messages": []}
        a = affinity_key_for(body, tenant="key-aaa")
        b = affinity_key_for(body, tenant="key-bbb")
        assert a and b and a != b
        assert affinity_key_for(body, tenant="key-aaa") == a

    def test_user_field_is_not_treated_as_a_cache_key(self) -> None:
        """`user` is an abuse-tracking id; using it pinned every one-shot too."""
        body = {"user": "u-42", "messages": [{"role": "user", "content": "hi"}]}
        assert affinity_key_for(body) == ""

    def test_key_is_stable_as_the_conversation_grows(self) -> None:
        """The whole point: turn 5 must land where turn 2 did.

        Hashing messages[:-1] changed the key every single turn, so the feature
        pinned nothing at all.
        """
        convo = [
            {"role": "system", "content": "stable preamble"},
            {"role": "user", "content": "one"},
        ]
        first = affinity_key_for({"messages": convo})
        keys = {first}
        for turn in range(2, 8):
            convo = [
                *convo,
                {"role": "assistant", "content": f"answer {turn}"},
                {"role": "user", "content": f"question {turn}"},
            ]
            keys.add(affinity_key_for({"messages": convo}))
        assert len(keys) == 1, f"key changed as the conversation grew: {len(keys)} distinct"

    def test_role_labels_cannot_forge_a_collision(self) -> None:
        """An unescaped join made two different conversations hash identically."""
        a = affinity_key_for(
            {"messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]}
        )
        b = affinity_key_for(
            {
                "messages": [
                    {"role": "user", "content": "a\nuser:b"},
                    {"role": "user", "content": "z"},
                ]
            }
        )
        assert a != b

    def test_empty_content_does_not_collapse_everyone_onto_one_hop(self) -> None:
        blank = affinity_key_for(
            {"messages": [{"role": "system", "content": ""}, {"role": "user", "content": ""}]}
        )
        assert blank == ""

    def test_repeated_conversation_lands_on_one_hop(self, vault: KeyVault) -> None:
        """Ten turns of one conversation must not scatter across the pool."""
        for i in range(4):
            _healthy_key(vault, f"aff-hop-{i}", priority=0)
        manager = FallbackManager(vault)
        convo = {
            "model": "auto",
            "messages": [
                {"role": "system", "content": "stable preamble"},
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"},
                {"role": "user", "content": "three"},
            ],
        }
        key = affinity_key_for(convo)
        chosen = {manager.ordered_candidates(affinity_key=key)[0].id for _ in range(10)}
        assert len(chosen) == 1

    def test_affinity_never_beats_priority(self, vault: KeyVault) -> None:
        """Health and the operator's priorities still decide; affinity breaks ties."""
        top = _healthy_key(vault, "priority-top", priority=0)
        for i in range(4):
            _healthy_key(vault, f"lower-{i}", priority=5)
        manager = FallbackManager(vault)
        for suffix in range(12):
            key = affinity_key_for(
                {
                    "messages": [
                        {"role": "user", "content": f"conversation {suffix}"},
                        {"role": "assistant", "content": "ok"},
                        {"role": "user", "content": "next"},
                    ]
                }
            )
            assert manager.ordered_candidates(affinity_key=key)[0].id == top.id

    def test_proxy_passes_affinity_without_changing_results(self, vault: KeyVault) -> None:
        _healthy_key(vault, "trace-hop")
        manager = FallbackManager(vault)
        trace = HopTrace()
        mock = _mock_client(_ok_response({"id": "traced", "choices": []}))
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            status, payload = asyncio.run(
                chat_completions(vault, manager, dict(_CHAT), trace=trace)
            )
        assert status == 200
        assert payload["id"] == "traced"
        assert trace.vault_key_id and trace.provider == "openai"
        assert trace.attempts == 1


# ─── Regressions found by adversarial review ─────────────────────────────────


class TestTierCannotFailOpen:
    def test_unknown_tier_is_not_issuable(self, client: TestClient) -> None:
        """`tier: "unlimited"` used to be accepted, then resolve to `local`."""
        resp = client.post("/api/apikeys", json={"label": "sneaky", "tier": "unlimited"})
        assert resp.status_code == 400
        assert "unknown tier" in resp.json()["detail"]

    def test_limiter_gives_an_unknown_tier_the_smallest_bucket(self) -> None:
        """Fail closed. tier_for used to fall back to `local` — the largest."""
        from openmw.openvault.vault.ratelimit import TokenBudgetLimiter

        limiter = TokenBudgetLimiter()
        unknown = limiter.tier_for("no-such-tier")
        assert unknown.tokens_per_min == limiter.tier_for("free").tokens_per_min
        assert unknown.tokens_per_min < limiter.tier_for("local").tokens_per_min

    def test_known_tiers_still_resolve(self) -> None:
        """R-0005: failing closed must not break tiers that do exist."""
        from openmw.openvault.vault.ratelimit import TokenBudgetLimiter

        assert TokenBudgetLimiter().tier_for("pro").tokens_per_min == 400_000


class TestControlPlaneIsGated:
    def _remote(self, vault: KeyVault) -> TestClient:
        app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
        return TestClient(app, client=("203.0.113.10", 5555))

    def test_remote_caller_cannot_mint_itself_a_key(self, vault: KeyVault) -> None:
        """An open mint endpoint hands out the credential the gateway requires."""
        resp = self._remote(vault).post("/api/apikeys", json={"label": "atk", "tier": "free"})
        assert resp.status_code == 403

    def test_remote_caller_cannot_enumerate_or_revoke_keys(self, vault: KeyVault) -> None:
        remote = self._remote(vault)
        assert remote.get("/api/apikeys").status_code == 403
        assert remote.delete("/api/apikeys/anything").status_code == 403

    def test_one_key_cannot_read_another_keys_spend(
        self, client: TestClient, vault: KeyVault
    ) -> None:
        """The api_key_id filter is a convenience, never the authorization."""
        _healthy_key(vault, "usage-hop")
        victim_id, victim_headers = issue_key(client, label="victim")
        client.post("/v1/chat/completions", json=_CHAT, headers=victim_headers)

        _snoop_id, snoop_headers = issue_key(client, label="snoop")
        snooped = client.get("/api/usage", params={"api_key_id": victim_id}, headers=snoop_headers)
        assert snooped.status_code == 200
        assert snooped.json()["count"] == 0, "a key holder must see only its own rows"

    def test_ratelimit_snapshot_reports_the_callers_real_tier(self, client: TestClient) -> None:
        """`?tier=local` used to answer 6M tpm to a caller holding 40k."""
        _key_id, headers = issue_key(client, tier="free")
        body = client.get(
            "/api/freeroute/ratelimit", params={"tier": "local"}, headers=headers
        ).json()
        assert body["tier"] == "free"
        assert body["token_capacity"] == 40_000


class TestBudgetResolution:
    def test_a_valid_sibling_field_survives_an_invalid_one(self) -> None:
        """{max_tokens: 0, max_completion_tokens: 2000} deleted BOTH, sending uncapped."""
        from openmw.openvault.vault.budget import prepare_hop_body

        decision = prepare_hop_body(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 0,
                "max_completion_tokens": 2000,
            },
            provider="openai",
            model="gpt-4o",
        )
        assert decision.body is not None
        assert "max_tokens" not in decision.body
        assert decision.body["max_completion_tokens"] == 2000

    def test_the_tightest_budget_wins(self) -> None:
        """First-match-wins widened a deliberate 100 cap to 8000."""
        from openmw.openvault.vault.budget import prepare_hop_body

        decision = prepare_hop_body(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 8000,
                "max_completion_tokens": 100,
            },
            provider="openai",
            model="gpt-4o",
        )
        assert decision.body is not None
        assert decision.body["max_tokens"] == 100
        assert decision.body["max_completion_tokens"] == 100

    def test_a_field_the_caller_did_not_send_is_not_invented(self) -> None:
        """Some providers 400 on an unexpected max_tokens."""
        from openmw.openvault.vault.budget import prepare_hop_body

        decision = prepare_hop_body(
            {"messages": [{"role": "user", "content": "hi"}], "max_completion_tokens": 256},
            provider="openai",
            model="gpt-4o",
        )
        assert decision.body is not None
        assert "max_tokens" not in decision.body
        assert decision.body["max_completion_tokens"] == 256

    def test_fractional_budget_is_invalid_not_zero(self) -> None:
        """value<=0 tested before int() let 0.5 through as a forwarded 0."""
        from openmw.openvault.vault.budget import prepare_hop_body

        decision = prepare_hop_body(
            {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 0.5},
            provider="openai",
            model="gpt-4o",
        )
        assert decision.body is not None
        assert "max_tokens" not in decision.body

    def test_reasoning_floor_above_the_ceiling_skips_the_hop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Under the floor a reasoning model returns empty content with HTTP 200.

        classify_attempt calls any 2xx a success, so the walk would stop on a hop
        that answered nothing at all.
        """
        from openmw.openvault.vault.budget import prepare_hop_body
        from openmw.openvault.vault.providers import MIN_REASONING_BUDGET, get_provider

        spec = get_provider("groq")
        assert spec is not None and spec.reasoning_models
        monkeypatch.setenv("OPENVAULT_CONTEXT_WINDOWS", '{"groq": 1000}')

        decision = prepare_hop_body(
            {"messages": [{"role": "user", "content": "x" * 3600}], "max_tokens": 32},
            provider="groq",
            model=spec.reasoning_models[0],
        )
        assert decision.body is None, "a hop that cannot fit the reasoning floor must be skipped"
        assert str(MIN_REASONING_BUDGET) in decision.refusal

    def test_image_parts_and_tool_calls_are_counted(self) -> None:
        """Counting them as ~0 made the refusal gate blind to the biggest inputs."""
        from openmw.openvault.vault.budget import estimate_tokens_for_body

        plain = estimate_tokens_for_body({"messages": [{"role": "user", "content": "hi"}]})
        with_image = estimate_tokens_for_body(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "data:" + "A" * 4000}}
                        ],
                    }
                ]
            }
        )
        assert with_image > plain * 10


class TestContextRefusalScope:
    def test_one_blocked_hop_does_not_refuse_the_whole_request(
        self, client: TestClient, vault: KeyVault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 400 "nobody can serve this" when a bigger-window hop was never asked."""
        monkeypatch.setenv("OPENVAULT_CONTEXT_WINDOWS", '{"openai": 500}')
        _healthy_key(vault, "small-window", provider="openai", priority=0)
        _healthy_key(vault, "big-window", provider="groq", priority=1)
        _key_id, headers = issue_key(client)

        big = {"model": "auto", "messages": [{"role": "user", "content": "x" * 8000}]}
        mock = _mock_client(_ok_response({"id": "served", "choices": []}))
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            resp = client.post("/v1/chat/completions", json=big, headers=headers)
        assert resp.status_code == 200, (
            "the groq hop has no configured window, so it must still be tried"
        )
        assert mock.post.await_count == 1
