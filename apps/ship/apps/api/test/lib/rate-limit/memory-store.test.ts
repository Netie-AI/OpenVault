import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryRateLimitStore } from "../../../src/lib/rate-limit/memory-store";

/**
 * Tests for the in-memory sliding-window-counter store.
 *
 * The store reads `Date.now()` directly, so we drive boundary cases with
 * `vi.useFakeTimers()` + `vi.setSystemTime(...)`. This is more reliable than
 * sleeping in tests and exercises the exact window-rollover logic.
 *
 * A small recap of the algorithm (lifted from types.ts):
 *
 *   estimated_rate(now)
 *     = count_in_current_window
 *     + count_in_previous_window * (1 - elapsed_in_current_window / window)
 *
 * A request is allowed iff `estimated < limit`. When allowed, count++.
 * When rejected, state is unchanged.
 */

describe("MemoryRateLimitStore.checkAndIncrement", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Pin "now" to a value aligned on a window boundary later in code:
    // each test recomputes its own window based on this anchor.
    vi.setSystemTime(new Date("2026-06-24T12:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── 1 ───────────────────────────────────────────────────────────────────
  it("fresh bucket: first request returns allowed with remaining=limit-1", async () => {
    const store = new MemoryRateLimitStore();
    const res = await store.checkAndIncrement("k", 1000, 5);

    expect(res.allowed).toBe(true);
    expect(res.remaining).toBe(4);
    expect(res.resetMs).toBeGreaterThan(0);
    expect(res.resetMs).toBeLessThanOrEqual(1000);
  });

  // ── 2 ───────────────────────────────────────────────────────────────────
  it("limit boundary: limit=3 allows exactly 3 then rejects the 4th", async () => {
    const store = new MemoryRateLimitStore();
    const r1 = await store.checkAndIncrement("k", 1000, 3);
    const r2 = await store.checkAndIncrement("k", 1000, 3);
    const r3 = await store.checkAndIncrement("k", 1000, 3);
    const r4 = await store.checkAndIncrement("k", 1000, 3);

    expect(r1.allowed).toBe(true);
    expect(r2.allowed).toBe(true);
    expect(r3.allowed).toBe(true);
    expect(r4.allowed).toBe(false);
    expect(r4.remaining).toBe(0);
  });

  // ── 3 ───────────────────────────────────────────────────────────────────
  it("reject does NOT increment: repeated rejections stay at remaining=0", async () => {
    const store = new MemoryRateLimitStore();
    // Fill the bucket.
    for (let i = 0; i < 3; i++) {
      await store.checkAndIncrement("k", 1000, 3);
    }

    const rejects = [];
    for (let i = 0; i < 5; i++) {
      rejects.push(await store.checkAndIncrement("k", 1000, 3));
    }

    for (const r of rejects) {
      expect(r.allowed).toBe(false);
      expect(r.remaining).toBe(0);
    }
  });

  // ── 4 ───────────────────────────────────────────────────────────────────
  it("rolled-forward window: prior count becomes previousCount, current resets to 0", async () => {
    const store = new MemoryRateLimitStore();
    const windowMs = 1000;
    const limit = 10;

    // Align to a clean window boundary so behaviour is deterministic.
    const aligned = Math.floor(Date.now() / windowMs) * windowMs;
    vi.setSystemTime(aligned);

    // Fill 10 requests in window 1 (uses entire limit).
    for (let i = 0; i < 10; i++) {
      await store.checkAndIncrement("k", windowMs, limit);
    }

    // Advance into the *next* window at +0% elapsed — previousCount=10,
    // weight=1.0, so estimated = 10 → not allowed.
    vi.setSystemTime(aligned + windowMs);
    const atBoundary = await store.checkAndIncrement("k", windowMs, limit);
    expect(atBoundary.allowed).toBe(false);

    // Advance to half-way into next window. estimated = 0 + 10 * (1 - 0.5) = 5 → allowed.
    vi.setSystemTime(aligned + windowMs + windowMs / 2);
    const halfway = await store.checkAndIncrement("k", windowMs, limit);
    expect(halfway.allowed).toBe(true);
  });

  // ── 5 ───────────────────────────────────────────────────────────────────
  it("sliding-window smoothing: weighted blend gates the start of the next window", async () => {
    const store = new MemoryRateLimitStore();
    const windowMs = 1000;
    const limit = 10;

    const aligned = Math.floor(Date.now() / windowMs) * windowMs;
    vi.setSystemTime(aligned);

    // Fill window 1 to the cap.
    for (let i = 0; i < 10; i++) {
      await store.checkAndIncrement("k", windowMs, limit);
    }

    // 5% into window 2: estimated = 0 + 10 * (1 - 0.05) = 9.5 < 10 → allowed.
    vi.setSystemTime(aligned + windowMs + 50);
    const fivePct = await store.checkAndIncrement("k", windowMs, limit);
    expect(fivePct.allowed).toBe(true);

    // After that allowed request, current=1, previous=10.
    // At 5% elapsed still: estimated = 1 + 10 * 0.95 = 10.5 ≥ 10 → rejected.
    const nextAtFivePct = await store.checkAndIncrement("k", windowMs, limit);
    expect(nextAtFivePct.allowed).toBe(false);
  });

  // ── 6 ───────────────────────────────────────────────────────────────────
  it("multi-key isolation: keys do not share buckets", async () => {
    const store = new MemoryRateLimitStore();

    // Drain key A.
    for (let i = 0; i < 3; i++) {
      await store.checkAndIncrement("A", 1000, 3);
    }
    const aReject = await store.checkAndIncrement("A", 1000, 3);
    expect(aReject.allowed).toBe(false);

    // B is independent — still fresh.
    const bFirst = await store.checkAndIncrement("B", 1000, 3);
    expect(bFirst.allowed).toBe(true);
    expect(bFirst.remaining).toBe(2);
  });

  // ── 7 ───────────────────────────────────────────────────────────────────
  it("windowMs=0 throws", async () => {
    const store = new MemoryRateLimitStore();
    await expect(store.checkAndIncrement("k", 0, 1)).rejects.toThrow(
      /windowMs must be > 0/,
    );
  });

  // ── 8 ───────────────────────────────────────────────────────────────────
  it("windowMs<0 throws", async () => {
    const store = new MemoryRateLimitStore();
    await expect(store.checkAndIncrement("k", -100, 1)).rejects.toThrow(
      /windowMs must be > 0/,
    );
  });

  // ── 9 ───────────────────────────────────────────────────────────────────
  it("after 2x window of inactivity, the bucket is treated as fresh", async () => {
    const store = new MemoryRateLimitStore();
    const windowMs = 1000;
    const limit = 3;

    const aligned = Math.floor(Date.now() / windowMs) * windowMs;
    vi.setSystemTime(aligned);

    // Fill the limit, then verify reject.
    for (let i = 0; i < 3; i++) {
      await store.checkAndIncrement("k", windowMs, limit);
    }
    const rejected = await store.checkAndIncrement("k", windowMs, limit);
    expect(rejected.allowed).toBe(false);

    // Skip far past 2 * windowMs of inactivity — both counters reset.
    vi.setSystemTime(aligned + 5 * windowMs);
    const fresh = await store.checkAndIncrement("k", windowMs, limit);
    expect(fresh.allowed).toBe(true);
    expect(fresh.remaining).toBe(limit - 1);
  });
});
