/**
 * Generic in-memory rate limiter - fixed window, per-key token bucket.
 *
 * Created via `createRateLimiter({ windowMs, max })`. Each `hit(key)` increments
 * the bucket for `key`. Once `max` is reached inside `windowMs`, further hits
 * return `{ ok: false, retryAfter }` until the window rolls.
 *
 * Used for:
 *   - per-IP sign-in attempts (5 per 5 min)
 *   - per-email sign-in failures (10 per 15 min - catches distributed
 *     credential stuffing where each request comes from a different IP)
 *
 * Why in-memory rather than Redis/sqlite:
 *   The Zero server is single-process by design (one VPS = one instance).
 *   Replicas would need a shared store, but that's not the current shape.
 *   When that day comes, swap the Map for `ioredis` and keep the interface.
 *
 * GC: a timer drops expired buckets every `windowMs`. The timer is `unref()`d
 * so it doesn't keep the Bun process alive on shutdown.
 */

export interface RateLimitOptions {
  /** Length of the fixed window in milliseconds. */
  windowMs: number;
  /** Allowed hits per window. The (max+1)th hit is rejected. */
  max: number;
}

export interface RateLimitResult {
  /** True if the hit was accepted, false if the bucket is full. */
  ok: boolean;
  /** Hits remaining in this window (0 once the limit is hit). */
  remaining: number;
  /** Seconds until the window rolls. 0 when `ok` is true. */
  retryAfter: number;
}

export interface RateLimiter {
  hit(key: string): RateLimitResult;
  reset(key: string): void;
  /** Stops the GC timer. Call from tests; production never needs this. */
  destroy(): void;
}

type Bucket = { count: number; resetAt: number };

export function createRateLimiter(opts: RateLimitOptions): RateLimiter {
  const buckets = new Map<string, Bucket>();

  const gc = setInterval(() => {
    const now = Date.now();
    for (const [key, bucket] of buckets) {
      if (bucket.resetAt <= now) buckets.delete(key);
    }
  }, opts.windowMs);
  if (typeof gc.unref === 'function') gc.unref();

  return {
    hit(key: string): RateLimitResult {
      const now = Date.now();
      const existing = buckets.get(key);
      if (!existing || existing.resetAt <= now) {
        buckets.set(key, { count: 1, resetAt: now + opts.windowMs });
        return { ok: true, remaining: opts.max - 1, retryAfter: 0 };
      }
      if (existing.count >= opts.max) {
        return {
          ok: false,
          remaining: 0,
          retryAfter: Math.max(1, Math.ceil((existing.resetAt - now) / 1000)),
        };
      }
      existing.count += 1;
      return {
        ok: true,
        remaining: opts.max - existing.count,
        retryAfter: 0,
      };
    },
    reset(key: string): void {
      buckets.delete(key);
    },
    destroy(): void {
      clearInterval(gc);
      buckets.clear();
    },
  };
}
