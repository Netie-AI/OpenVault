"""RedisBucketStore — unit tests with fakeredis when available."""

from __future__ import annotations

import pytest

from openmw.openvault.vault.ratelimit import TierLimits, TokenBudgetLimiter
from openmw.openvault.vault.redis_store import RedisBucketStore, try_make_redis_store


def test_try_make_redis_store_none_without_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENVAULT_REDIS_URL", raising=False)
    assert try_make_redis_store() is None


def test_redis_dual_bucket_atomic_with_fakeredis() -> None:
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeRedis()
    store = RedisBucketStore(client)
    clock = {"t": 1000.0}

    def now() -> float:
        return clock["t"]

    tiers = {"t": TierLimits("t", requests_per_min=2, tokens_per_min=100)}
    limiter = TokenBudgetLimiter(tiers, store=store, default_tier="t", clock=now)

    a = limiter.reserve("id1", prompt_tokens=10, max_tokens=10)
    b = limiter.reserve("id1", prompt_tokens=10, max_tokens=10)
    c = limiter.reserve("id1", prompt_tokens=10, max_tokens=10)
    assert a.allowed and b.allowed
    assert c.allowed is False
    assert c.limited_by == "request"

    # Token budget: refill request bucket, burn tokens
    clock["t"] += 60.0
    big = limiter.reserve("id1", prompt_tokens=80, max_tokens=30)  # 110 > 100
    assert big.allowed is False
    assert big.limited_by == "token"

    ok = limiter.reserve("id1", prompt_tokens=40, max_tokens=10)  # 50
    assert ok.allowed is True
    limiter.settle("id1", reserved_tokens=ok.reserved_tokens, actual_tokens=20)
    snap = limiter.status("id1")
    assert snap["backend"] == "RedisBucketStore"
    assert "remaining_tokens" in snap
    assert snap["remaining_tokens"] == snap["token_remaining"]
