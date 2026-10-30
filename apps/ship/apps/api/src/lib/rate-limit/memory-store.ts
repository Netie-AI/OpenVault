/**
 * In-memory RateLimitStore — sliding-window-counter algorithm.
 *
 * State per key: { windowStart, count, previousCount }. Two counters
 * are sufficient — the current window's count + the previous window's
 * count. The estimated rate is a weighted blend (Cloudflare's approach).
 *
 * Memory bounded by `maxKeys`. When the cap is hit we sweep expired
 * entries first (insertion-order iteration → effectively LRU-ish), and
 * if still over the cap drop the oldest insertion-order batch. Same
 * shape as the old in-memory rate limiter, just behind a proper
 * interface and with smoothing.
 */

import type { RateLimitResult, RateLimitStore } from "./types";

interface Bucket {
  windowStart: number;
  count: number;
  previousCount: number;
}

const DEFAULT_MAX_KEYS = 10_000;
const EVICT_BATCH = 1024;

export class MemoryRateLimitStore implements RateLimitStore {
  readonly name = "memory" as const;
  private readonly maxKeys: number;
  private readonly buckets = new Map<string, Bucket>();

  constructor(opts: { maxKeys?: number } = {}) {
    this.maxKeys = opts.maxKeys ?? DEFAULT_MAX_KEYS;
  }

  async checkAndIncrement(
    key: string,
    windowMs: number,
    limit: number,
  ): Promise<RateLimitResult> {
    if (windowMs <= 0) {
      throw new Error(`rate-limit: windowMs must be > 0 (got ${windowMs})`);
    }
    const now = Date.now();
    this.prune(now, windowMs);

    const currentWindow = Math.floor(now / windowMs) * windowMs;
    const elapsedFraction = (now - currentWindow) / windowMs;

    let bucket = this.buckets.get(key);
    if (!bucket || bucket.windowStart < currentWindow - windowMs) {
      // No bucket, or both windows are older than "previous" — start fresh.
      bucket = { windowStart: currentWindow, count: 0, previousCount: 0 };
      this.buckets.set(key, bucket);
    } else if (bucket.windowStart < currentWindow) {
      // We've rolled forward exactly one window — current becomes previous,
      // current resets to 0.
      const previousCount =
        bucket.windowStart === currentWindow - windowMs
          ? bucket.count
          : 0;
      bucket = { windowStart: currentWindow, count: 0, previousCount };
      this.buckets.set(key, bucket);
    }

    const estimated = bucket.count + bucket.previousCount * (1 - elapsedFraction);
    const allowed = estimated < limit;

    if (allowed) {
      bucket.count += 1;
    }

    const remaining = Math.max(0, Math.floor(limit - estimated - (allowed ? 1 : 0)));
    const resetMs = Math.max(0, currentWindow + windowMs - now);
    return { allowed, remaining, resetMs };
  }

  async dispose(): Promise<void> {
    this.buckets.clear();
  }

  private prune(now: number, windowMs: number): void {
    if (this.buckets.size <= this.maxKeys) return;
    const cutoff = now - 2 * windowMs;
    for (const [k, b] of this.buckets) {
      if (b.windowStart < cutoff) this.buckets.delete(k);
      if (this.buckets.size <= this.maxKeys - EVICT_BATCH) return;
    }
    let toEvict = this.buckets.size - (this.maxKeys - EVICT_BATCH);
    for (const k of this.buckets.keys()) {
      if (toEvict-- <= 0) break;
      this.buckets.delete(k);
    }
  }
}
