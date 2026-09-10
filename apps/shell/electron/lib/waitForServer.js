/**
 * waitForServer.js - bounded readiness probe for a local HTTP server.
 *
 * Why this exists as its own module:
 *
 * The previous probe lived inline in main-openvault.js and looked bounded:
 *
 *     while (Date.now() - start < timeoutMs) {
 *       try { const res = await fetch(url); ... } catch {}
 *       await sleep(500);
 *     }
 *
 * That bound is decorative. The while-condition is only re-evaluated between
 * attempts, and `fetch` was given no per-attempt deadline, so a server that
 * completes the TCP handshake and then never sends a byte leaves exactly one
 * fetch pending forever. The loop never comes back round, `timeoutMs` never
 * fires, and the caller's "did it start?" branch never runs.
 *
 * That is not hypothetical. A `next dev` on :3010 that has been up long enough
 * to wedge accepts the socket and answers nothing - measured at zero bytes
 * after 180s on every path, including `/`. Against that server the Electron
 * shell never opened a window and never showed its own "did not start" dialog:
 * the app simply did nothing, forever, which is the silent degradation
 * R-0011 exists to forbid.
 *
 * The fix is at the primitive, not at one call site: every attempt carries its
 * own deadline, so the overall deadline is always reachable. A caller cannot
 * reintroduce the hang by passing a different URL.
 *
 * The probe also reports WHICH failure happened, because the two need opposite
 * remedies and "not ready" cannot tell them apart:
 *   - "unreachable": nothing is listening. Start the server.
 *   - "stalled":     something is listening and not answering. Stop the stale
 *                    process; starting another one will just fail to bind.
 *
 * Pure and Electron-free so it can be unit-tested against a real socket
 * without the Electron binary (same reasoning as resolveServerEntry.js).
 */

const DEFAULT_TIMEOUT_MS = 180000;

/**
 * One attempt's budget.
 *
 * Sized from measurement, not taste: a cold Turbopack compile of /vault on the
 * exFAT drive this repo lives on took 17.5s to return 200, and Next itself
 * prints "Slow filesystem detected" there. A 5s attempt budget would abort a
 * server that was working correctly and merely slow, which is a control that
 * refuses legitimate work (R-0005). 30s clears the measured cold compile with
 * room to spare while still leaving the overall deadline reachable - a wedged
 * server burns four of these inside the shell's 120s web budget and is then
 * reported, rather than waited on forever.
 */
const DEFAULT_ATTEMPT_TIMEOUT_MS = 30000;

/** Gap between attempts. */
const DEFAULT_INTERVAL_MS = 500;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isTimeoutError(err) {
  if (!err || typeof err !== "object") return false;
  const name = err.name || (err.cause && err.cause.name) || "";
  return name === "TimeoutError" || name === "AbortError";
}

function describe(err) {
  if (!err) return "unknown error";
  if (typeof err === "string") return err;
  return err.message || String(err);
}

/**
 * Poll `url` until it answers or the deadline passes.
 *
 * @param {string} url
 * @param {number} [timeoutMs] overall wall-clock budget
 * @param {object} [options]
 * @param {number} [options.attemptTimeoutMs] per-attempt budget
 * @param {number} [options.intervalMs] gap between attempts
 * @param {Function} [options.fetchFn] injectable fetch
 * @param {Function} [options.onProgress] called with each state change, so the
 *        caller can surface a visible waiting state instead of dead silence
 * @returns {Promise<{ok: boolean, reason: string, attempts: number,
 *                    stalledAttempts: number, elapsedMs: number,
 *                    lastError: string|null}>}
 */
async function waitForServer(url, timeoutMs = DEFAULT_TIMEOUT_MS, options = {}) {
  const {
    attemptTimeoutMs = DEFAULT_ATTEMPT_TIMEOUT_MS,
    intervalMs = DEFAULT_INTERVAL_MS,
    fetchFn = globalThis.fetch,
    onProgress = () => {},
  } = options;

  const start = Date.now();
  const deadline = start + timeoutMs;
  let attempts = 0;
  let stalledAttempts = 0;
  let lastError = null;

  const result = (ok, reason) => ({
    ok,
    reason,
    attempts,
    stalledAttempts,
    elapsedMs: Date.now() - start,
    lastError,
  });

  while (Date.now() < deadline) {
    attempts += 1;

    // Never let one attempt outlive the overall deadline, and never let it run
    // unbounded. This clamp is what makes `timeoutMs` real.
    const remaining = deadline - Date.now();
    const budget = Math.max(1, Math.min(attemptTimeoutMs, remaining));
    // A final attempt squeezed into the last few milliseconds will abort on its
    // own truncated budget. That is not evidence the server stalled, and
    // counting it as one would send the user to kill a process that was
    // answering fine.
    const budgetWasTruncated = budget < attemptTimeoutMs;

    try {
      const res = await fetchFn(url, { signal: AbortSignal.timeout(budget) });
      // Any answer at all proves the listener is alive and serving. A 5xx from
      // a dev server is still "up" - it compiled and chose to fail.
      if (res.ok || res.status < 500) {
        onProgress({ state: "ready", attempts, elapsedMs: Date.now() - start });
        return result(true, "ready");
      }
      lastError = "HTTP " + res.status;
      onProgress({
        state: "unreachable",
        attempts,
        elapsedMs: Date.now() - start,
        detail: lastError,
      });
    } catch (err) {
      const truncatedAbort = isTimeoutError(err) && budgetWasTruncated;
      const stalled = isTimeoutError(err) && !budgetWasTruncated;
      if (stalled) stalledAttempts += 1;
      // A truncated final attempt aborts on our own clamp, not on anything the
      // server did. Letting it overwrite lastError would replace a real cause
      // ("HTTP 503", "ECONNREFUSED") with a timeout message we manufactured.
      if (!truncatedAbort || lastError === null) lastError = describe(err);
      onProgress({
        state: stalled ? "stalled" : "unreachable",
        attempts,
        elapsedMs: Date.now() - start,
        detail: lastError,
      });
    }

    if (Date.now() >= deadline) break;
    await sleep(Math.min(intervalMs, Math.max(0, deadline - Date.now())));
  }

  // A server that stalled at least once is a different fault from one that was
  // never listening, and the caller must be able to say so out loud.
  return result(false, stalledAttempts > 0 ? "stalled" : "unreachable");
}

module.exports = {
  waitForServer,
  DEFAULT_TIMEOUT_MS,
  DEFAULT_ATTEMPT_TIMEOUT_MS,
  DEFAULT_INTERVAL_MS,
};
