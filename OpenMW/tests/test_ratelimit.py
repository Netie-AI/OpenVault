"""OpenFree token-budget rate limiter — unit + gateway tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.ratelimit import (
    TierLimits,
    TokenBudgetLimiter,
    estimate_prompt_tokens,
    usage_total_tokens,
)
from openmw.openvault.vault.store import KeyVault


class FakeClock:
    """Deterministic monotonic clock for refill math."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def vault(tmp_path: Path) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "openvault" / "keys.db", seal=seal)


def _limiter(
    clock: FakeClock, *, requests_per_min: float, tokens_per_min: float
) -> TokenBudgetLimiter:
    tiers = {"t": TierLimits("t", requests_per_min=requests_per_min, tokens_per_min=tokens_per_min)}
    return TokenBudgetLimiter(tiers, default_tier="t", clock=clock)


def test_estimate_prompt_tokens() -> None:
    assert estimate_prompt_tokens([]) == 1
    assert estimate_prompt_tokens([{"role": "user", "content": "a" * 400}]) == 100
    multimodal = [{"role": "user", "content": [{"type": "text", "text": "a" * 40}]}]
    assert estimate_prompt_tokens(multimodal) == 10


def test_usage_total_tokens() -> None:
    assert usage_total_tokens({"usage": {"total_tokens": 42}}) == 42
    assert usage_total_tokens({"usage": {"prompt_tokens": 10, "completion_tokens": 7}}) == 17
    assert usage_total_tokens({"choices": []}) is None
    assert usage_total_tokens("raw text") is None


def test_token_budget_blocks_oversized_request() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, requests_per_min=60, tokens_per_min=6000)
    decision = limiter.reserve("u1", prompt_tokens=10, max_tokens=10_000)  # 10_010 > 6_000
    assert decision.allowed is False
    assert decision.limited_by == "token"
    assert decision.retry_after_s > 0
    headers = decision.headers()
    assert headers["X-RateLimit-Tier"] == "t"
    assert headers["X-RateLimit-Limit"] == "6000"
    assert "Retry-After" in headers


def test_request_bucket_blocks_qps_flood() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, requests_per_min=3, tokens_per_min=10_000_000)
    allowed = [limiter.reserve("u", prompt_tokens=1, max_tokens=1).allowed for _ in range(4)]
    assert allowed == [True, True, True, False]
    denied = limiter.reserve("u", prompt_tokens=1, max_tokens=1)
    assert denied.limited_by == "request"
    assert denied.retry_after_s > 0


def test_settle_refunds_unused_tokens() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, requests_per_min=60, tokens_per_min=6000)
    decision = limiter.reserve("u", prompt_tokens=100, max_tokens=1000)  # reserve 1100
    assert decision.allowed
    assert limiter.status("u")["token_remaining"] == 6000 - 1100
    limiter.settle("u", reserved_tokens=decision.reserved_tokens, actual_tokens=250)
    # refunded 1100 - 250 -> only 250 truly spent
    assert limiter.status("u")["token_remaining"] == 6000 - 250


def test_token_bucket_refills_smoothly() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, requests_per_min=10_000, tokens_per_min=6000)  # 100 tok/s
    drain = limiter.reserve("u", prompt_tokens=0, max_tokens=6000)
    assert drain.allowed
    assert limiter.status("u")["token_remaining"] == 0
    assert limiter.reserve("u", prompt_tokens=0, max_tokens=6000).allowed is False
    clock.advance(30.0)  # ~3000 tokens back, no fixed-window reset
    assert limiter.reserve("u", prompt_tokens=0, max_tokens=3000).allowed is True


def test_isolated_identities_do_not_share_budget() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, requests_per_min=1, tokens_per_min=10_000)
    assert limiter.reserve("alice", prompt_tokens=1, max_tokens=1).allowed is True
    assert limiter.reserve("alice", prompt_tokens=1, max_tokens=1).allowed is False
    # bob has his own bucket
    assert limiter.reserve("bob", prompt_tokens=1, max_tokens=1).allowed is True


def test_gateway_returns_429_when_request_bucket_drained(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    client = TestClient(app)
    headers = {"x-openfree-tier": "free", "x-openfree-identity": "tester"}
    body = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
    saw_429 = False
    for _ in range(25):  # free tier allows 20 requests/min before the QPS bucket drains
        resp = client.post("/v1/chat/completions", json=body, headers=headers)
        if resp.status_code == 429:
            saw_429 = True
            assert resp.headers.get("Retry-After") is not None
            assert resp.headers.get("X-RateLimit-Tier") == "free"
            assert resp.json()["error"]["type"] == "rate_limited"
            break
        # allowed calls hit the empty fallback pool -> 503 (no network)
        assert resp.status_code in (502, 503)
    assert saw_429, "expected the QPS bucket to trigger a 429 within 25 calls"


def test_openfree_ratelimit_status_endpoint(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    client = TestClient(app)
    resp = client.get("/api/openfree/ratelimit", params={"tier": "free", "identity": "x"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["tier"] == "free"
    assert data["token_capacity"] == 40_000
    assert data["request_capacity"] == 20
    assert "free" in data["tiers"]
    assert data["backend"] == "InMemoryBucketStore"
