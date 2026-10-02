/**
 * Redis-backed RateLimitStore — sliding-window-counter via a Lua script.
 *
 * State per key (Redis hash):
 *   { windowStart, count, previousCount }
 *
 * The check-and-increment must be atomic — without a Lua script, a
 * concurrent burst can let multiple requests slip past the limit. The
 * script runs single-threaded on the Redis server and either rejects
 * (no state mutation) or accepts (atomic INCR + TTL refresh).
 *
 * Key TTL is set to 2× window so a key not seen for a full window
 * naturally expires; the next request creates a fresh bucket.
 *
 * Availability invariant: every Redis call is bounded by COMMAND_TIMEOUT_MS.
 * The shared ioredis connection is configured with maxRetriesPerRequest=null,
 * which means commands would otherwise queue indefinitely on a disconnect —
 * stalling every API request, not just rate-limit checks. The timeout
 * lets the fail-open catch in `index.ts` actually fire.
 */

import type IORedis from "ioredis";
import type { RateLimitResult, RateLimitStore } from "./types";

const COMMAND_TIMEOUT_MS = 200;

/**
 * KEYS[1] = bucket key (already namespaced + policy + subject)
 * ARGV[1] = now (ms)
 * ARGV[2] = windowMs
 * ARGV[3] = limit
 *
 * Returns: { allowed (1|0), remaining, resetMs }
 *
 * Bucket layout:
 *   HSET key
 *     ws  -> current window start (ms)
 *     c   -> count in current window
 *     pc  -> count in previous window
 */
const CHECK_AND_INCR_LUA = `
local key = KEYS[1]
local now = tonumber(ARGV[1])
local windowMs = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])

local currentWindow = math.floor(now / windowMs) * windowMs
local elapsedFraction = (now - currentWindow) / windowMs

local stored = redis.call("HMGET", key, "ws", "c", "pc")
local ws = tonumber(stored[1]) or 0
local c = tonumber(stored[2]) or 0
local pc = tonumber(stored[3]) or 0

if ws < currentWindow - windowMs then
  -- Both windows older than previous — fresh start.
  ws = currentWindow
  c = 0
  pc = 0
elseif ws < currentWindow then
  -- Rolled forward exactly one window — current becomes previous.
  if ws == currentWindow - windowMs then
    pc = c
  else
    pc = 0
  end
  ws = currentWindow
  c = 0
end

local estimated = c + pc * (1 - elapsedFraction)
local allowed = estimated < limit
if allowed then
  c = c + 1
end

redis.call("HSET", key, "ws", ws, "c", c, "pc", pc)
redis.call("PEXPIRE", key, windowMs * 2)

local remaining = math.max(0, math.floor(limit - estimated - (allowed and 1 or 0)))
local resetMs = math.max(0, currentWindow + windowMs - now)
return { allowed and 1 or 0, remaining, resetMs }
`;

export class RedisRateLimitStore implements RateLimitStore {
  readonly name = "redis" as const;
  private readonly client: IORedis;
  private readonly namespace: string;
  /**
   * Single in-flight SCRIPT LOAD promise — memoized so concurrent first
   * calls don't each fire their own LOAD. Resolves to the SHA1 hex.
   * Reset to null on NOSCRIPT so the next caller bootstraps fresh.
   */
  private scriptShaPromise: Promise<string> | null = null;

  constructor(client: IORedis, namespace: string) {
    this.client = client;
    this.namespace = namespace;
  }

  async checkAndIncrement(
    key: string,
    windowMs: number,
    limit: number,
  ): Promise<RateLimitResult> {
    if (windowMs <= 0) {
      throw new Error(`rate-limit: windowMs must be > 0 (got ${windowMs})`);
    }
    const fullKey = `${this.namespace}:${key}`;
    const now = Date.now();
    const [allowedRaw, remaining, resetMs] = await this.evalScript(
      fullKey,
      now,
      windowMs,
      limit,
    );
    return {
      allowed: allowedRaw === 1,
      remaining: Number(remaining),
      resetMs: Number(resetMs),
    };
  }

  async dispose(): Promise<void> {
    // Connection is owned by the factory.
  }

  /**
   * Memoized SCRIPT LOAD — first caller does the work, concurrent
   * callers await the same promise. Errors clear the slot so the next
   * caller retries cleanly.
   */
  private loadScript(): Promise<string> {
    if (!this.scriptShaPromise) {
      this.scriptShaPromise = withTimeout(
        this.client.script("LOAD", CHECK_AND_INCR_LUA),
        COMMAND_TIMEOUT_MS,
        "SCRIPT LOAD",
      ).then((sha) => {
        if (typeof sha !== "string" || sha.length === 0) {
          throw new Error(`SCRIPT LOAD returned non-string: ${typeof sha}`);
        }
        return sha;
      }).catch((err) => {
        this.scriptShaPromise = null;
        throw err;
      });
    }
    return this.scriptShaPromise;
  }

  private async evalScript(
    fullKey: string,
    now: number,
    windowMs: number,
    limit: number,
  ): Promise<[number, number, number]> {
    // EVALSHA fast path. On NOSCRIPT (script evicted by FLUSHALL,
    // cluster failover, restart) — clear the memoized SHA, reload, retry.
    const args = [
      1,
      fullKey,
      String(now),
      String(windowMs),
      String(limit),
    ] as const;
    try {
      const sha = await this.loadScript();
      return (await withTimeout(
        this.client.evalsha(sha, ...args),
        COMMAND_TIMEOUT_MS,
        "EVALSHA",
      )) as [number, number, number];
    } catch (err) {
      const msg = (err as Error).message;
      if (msg.includes("NOSCRIPT")) {
        // Drop the cached SHA so the next caller (and this retry) reloads.
        this.scriptShaPromise = null;
        const sha = await this.loadScript();
        return (await withTimeout(
          this.client.evalsha(sha, ...args),
          COMMAND_TIMEOUT_MS,
          "EVALSHA (post-NOSCRIPT)",
        )) as [number, number, number];
      }
      throw err;
    }
  }
}

/**
 * Reject a promise that doesn't settle within `ms`. The rate-limit
 * factory wraps every store call in try/catch and fails open, so a
 * timeout here surfaces as "allow this one" — preferable to stalling
 * the request for as long as Redis takes to reconnect (which is
 * forever with maxRetriesPerRequest=null).
 */
function withTimeout<T>(
  p: Promise<T>,
  ms: number,
  label: string,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(`rate-limit: Redis ${label} timed out after ${ms}ms`)),
      ms,
    );
  });
  return Promise.race([p, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}
