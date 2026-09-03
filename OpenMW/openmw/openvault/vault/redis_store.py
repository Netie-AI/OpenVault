"""Redis + Lua dual-bucket store — cluster-safe FreeRoute budgets.

Atomic reserve/refund so multiple OpenVault gateway processes cannot
double-spend the same identity's request + token buckets.

Activate with ``OPENVAULT_REDIS_URL`` (e.g. ``redis://127.0.0.1:6379/0``).
If redis is unreachable or the package is missing, callers should fall back
to :class:`InMemoryBucketStore`.
"""

from __future__ import annotations

import math
from typing import Any

from openmw.openvault.vault.ratelimit import ReserveOutcome, TierLimits

# KEYS[1] = ov:rl:{tier}:{identity}
# HASH fields: rt (request tokens), ru (request updated_at), tt, tu
# ARGV: now, req_cap, req_refill, req_cost, tok_cap, tok_refill, tok_cost
_LUA_RESERVE = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local req_cap = tonumber(ARGV[2])
local req_refill = tonumber(ARGV[3])
local req_cost = tonumber(ARGV[4])
local tok_cap = tonumber(ARGV[5])
local tok_refill = tonumber(ARGV[6])
local tok_cost = tonumber(ARGV[7])

local vals = redis.call('HMGET', key, 'rt', 'ru', 'tt', 'tu')
local rt = tonumber(vals[1])
local ru = tonumber(vals[2])
local tt = tonumber(vals[3])
local tu = tonumber(vals[4])
if rt == nil then rt = req_cap end
if ru == nil then ru = now end
if tt == nil then tt = tok_cap end
if tu == nil then tu = now end

if now > ru and req_refill > 0 then
  rt = math.min(req_cap, rt + (now - ru) * req_refill)
  ru = now
end
if now > tu and tok_refill > 0 then
  tt = math.min(tok_cap, tt + (now - tu) * tok_refill)
  tu = now
end

local request_ok = (rt + 1e-9) >= req_cost
local token_ok = (tt + 1e-9) >= tok_cost

if request_ok and token_ok then
  rt = math.max(0, rt - req_cost)
  tt = math.max(0, tt - tok_cost)
  redis.call('HMSET', key, 'rt', rt, 'ru', ru, 'tt', tt, 'tu', tu)
  redis.call('EXPIRE', key, 86400)
  return {1, tok_cost, 0, rt, tt, tok_cap, ''}
end

local function retry_after(have, need, refill)
  local deficit = need - have
  if deficit <= 0 then return 0 end
  if refill <= 0 then return 1e9 end
  return deficit / refill
end

local req_retry = 0
if not request_ok then req_retry = retry_after(rt, req_cost, req_refill) end
local tok_retry = 0
if not token_ok then tok_retry = retry_after(tt, tok_cost, tok_refill) end

local limited = 'request'
local retry = req_retry
if (not token_ok) and (tok_retry >= req_retry) then
  limited = 'token'
  retry = tok_retry
end

redis.call('HMSET', key, 'rt', rt, 'ru', ru, 'tt', tt, 'tu', tu)
redis.call('EXPIRE', key, 86400)
return {0, 0, retry, rt, tt, tok_cap, limited}
"""

_LUA_REFUND = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local tok_cap = tonumber(ARGV[2])
local tok_refill = tonumber(ARGV[3])
local amount = tonumber(ARGV[4])

local vals = redis.call('HMGET', key, 'tt', 'tu')
local tt = tonumber(vals[1])
local tu = tonumber(vals[2])
if tt == nil then tt = tok_cap end
if tu == nil then tu = now end
if now > tu and tok_refill > 0 then
  tt = math.min(tok_cap, tt + (now - tu) * tok_refill)
  tu = now
end
tt = math.min(tok_cap, tt + math.max(0, amount))
redis.call('HMSET', key, 'tt', tt, 'tu', tu)
redis.call('EXPIRE', key, 86400)
return tt
"""

_LUA_SNAPSHOT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local req_cap = tonumber(ARGV[2])
local req_refill = tonumber(ARGV[3])
local tok_cap = tonumber(ARGV[4])
local tok_refill = tonumber(ARGV[5])

local vals = redis.call('HMGET', key, 'rt', 'ru', 'tt', 'tu')
local rt = tonumber(vals[1])
local ru = tonumber(vals[2])
local tt = tonumber(vals[3])
local tu = tonumber(vals[4])
if rt == nil then rt = req_cap end
if ru == nil then ru = now end
if tt == nil then tt = tok_cap end
if tu == nil then tu = now end
if now > ru and req_refill > 0 then
  rt = math.min(req_cap, rt + (now - ru) * req_refill)
  ru = now
end
if now > tu and tok_refill > 0 then
  tt = math.min(tok_cap, tt + (now - tu) * tok_refill)
  tu = now
end
redis.call('HMSET', key, 'rt', rt, 'ru', ru, 'tt', tt, 'tu', tu)
redis.call('EXPIRE', key, 86400)
return {rt, tt}
"""


class RedisBucketStore:
    """Atomic dual-bucket store backed by Redis EVAL scripts."""

    def __init__(self, redis_client: Any, *, key_prefix: str = "ov:rl:") -> None:
        self._r = redis_client
        self._prefix = key_prefix

    def _key(self, identity: str, tier: TierLimits) -> str:
        # Avoid key injection from identity
        safe = "".join(c if c.isalnum() or c in "._:-" else "_" for c in identity)[:128]
        return f"{self._prefix}{tier.name}:{safe}"

    def _eval(self, script: str, key: str, args: list[Any]) -> Any:
        # Prefer EVAL over EVALSHA so fakeredis / proxies without SCRIPT LOAD work.
        return self._r.eval(script, 1, key, *args)

    def reserve(
        self,
        identity: str,
        tier: TierLimits,
        request_cost: float,
        token_cost: float,
        now: float,
    ) -> ReserveOutcome:
        raw = self._eval(
            _LUA_RESERVE,
            self._key(identity, tier),
            [
                now,
                tier.request_capacity,
                tier.request_refill_per_sec,
                request_cost,
                tier.token_capacity,
                tier.token_refill_per_sec,
                token_cost,
            ],
        )
        allowed = int(raw[0]) == 1
        reserved = int(float(raw[1]))
        retry = float(raw[2])
        if math.isinf(retry) or retry >= 1e9:
            retry = math.inf
        req_rem = float(raw[3])
        tok_rem = float(raw[4])
        tok_cap = float(raw[5])
        limited = raw[6]
        if isinstance(limited, bytes):
            limited = limited.decode("utf-8", errors="replace")
        limited_s = str(limited or "")
        return ReserveOutcome(
            allowed=allowed,
            reserved_tokens=reserved if allowed else 0,
            retry_after_s=0.0 if allowed else retry,
            request_remaining=req_rem,
            token_remaining=tok_rem,
            token_capacity=tok_cap,
            limited_by="" if allowed else limited_s,
        )

    def refund(self, identity: str, tier: TierLimits, token_amount: float, now: float) -> None:
        if token_amount <= 0:
            return
        self._eval(
            _LUA_REFUND,
            self._key(identity, tier),
            [now, tier.token_capacity, tier.token_refill_per_sec, token_amount],
        )

    def snapshot(self, identity: str, tier: TierLimits, now: float) -> tuple[float, float]:
        raw = self._eval(
            _LUA_SNAPSHOT,
            self._key(identity, tier),
            [
                now,
                tier.request_capacity,
                tier.request_refill_per_sec,
                tier.token_capacity,
                tier.token_refill_per_sec,
            ],
        )
        return float(raw[0]), float(raw[1])


def try_make_redis_store(url: str | None = None) -> RedisBucketStore | None:
    """Build a Redis store from ``OPENVAULT_REDIS_URL`` or ``url``. None if unavailable."""
    import os

    redis_url = (url or os.environ.get("OPENVAULT_REDIS_URL") or "").strip()
    if not redis_url:
        return None
    try:
        import redis  # type: ignore[import-untyped]
    except ImportError:
        return None
    try:
        client = redis.Redis.from_url(redis_url, decode_responses=False)
        client.ping()
    except Exception:
        return None
    return RedisBucketStore(client)
