/**
 * SSE wire format — the ONE place that knows how bytes become frames.
 *
 * ============================================================================
 * FRAME CONTRACT — `GET /api/ship/engine/{id}/stream` (plan §3, row `/ship/deploy/[id]`)
 * ============================================================================
 * The Python producer has not been written yet. It will be written to match
 * THIS file, so any divergence is a silent, expensive bug: the terminal would
 * simply render nothing and the pane would sit on "Streaming" forever. Both
 * ends are therefore stated here explicitly.
 *
 * Transport:
 *   - `text/event-stream`, frames separated by a BLANK LINE (`\n\n`).
 *   - Each frame carries exactly one JSON object on `data:` lines. Multi-line
 *     `data:` is joined with `\n` before parsing, per the SSE spec.
 *   - `event:` is optional. When present and the JSON omits `type`, the event
 *     name supplies it.
 *   - `id:` is optional. When present and the JSON omits `eventId`, a numeric
 *     `id:` supplies it (SSE's native Last-Event-ID path).
 *   - Lines starting with `:` are comments/heartbeats and are ignored.
 *   - The producer SHOULD terminate the final frame with a blank line, but this
 *     parser also flushes a trailing frame at stream close — see `flush` in the
 *     consumers. A producer that forgets the trailing newline must not be able
 *     to strand the UI.
 *
 * JSON payload:
 *   { type: "log" | "phase" | "progress" | "complete" | "end" | "error"
 *           | "started" | "connected" | "reconnected" | "cancelled"
 *           | "prompt" | "service-status",
 *     data?:        string,   // base64 of RAW terminal bytes (ANSI intact)
 *     eventId?:     number,   // monotonic; assigned BEFORE ring-buffer trim
 *     step?:        string,   // "prepare"|"clone"|"install"|"build"|"deploy"
 *     stepStatus?:  "running"|"completed"|"failed"|"skipped",
 *     currentStep?: number,   // index; see steps.ts STEP_INDEX
 *     progress?:    number,   // 0-100; see steps.ts STEP_PROGRESS
 *     success?:     boolean,  // REQUIRED on `complete` — see below
 *     exitCode?:    number,
 *     error?, errorCode?, errorDetails?, message?, phase? }
 *
 * Load-bearing rules the producer MUST honour:
 *   1. `eventId` is assigned before the ring buffer trims, never after. It is
 *      the dedup key on reconnect; a reused id repaints log lines.
 *   2. `data` is base64 of raw bytes, NOT of a UTF-8 string. Log output carries
 *      ANSI escapes and half-finished UTF-8 sequences that JSON cannot hold.
 *   3. `complete` MUST carry `success`. If it is omitted this client refuses to
 *      guess in the optimistic direction — see `classifyFrame` — because
 *      reporting a deploy as succeeded on missing evidence is the one failure
 *      mode we will not ship.
 *   4. Exactly one terminal frame per stream (`complete`, `error`, `cancelled`,
 *      or `end` with a non-zero exit code). The stream may then close.
 * ============================================================================
 *
 * This module imports nothing on purpose: it is pure, dependency-free logic so
 * it can be executed directly by `node selfcheck.mts` without Next, the `@/`
 * path alias, or a test runner (D: is exFAT — see apps/README.md — so adding a
 * package-managed test framework is not on the table).
 */

/** One parsed SSE frame: the event name, the optional `id:`, and its JSON. */
export interface RawSSEFrame {
  /** SSE `event:` name, or `"message"` when the producer omitted it. */
  event: string;
  /** SSE `id:` field verbatim, when present. */
  id?: string;
  /** The frame's decoded JSON object. */
  data: Record<string, unknown>;
}

/** Frame blocks carved out of a buffer, plus the incomplete tail to keep. */
export interface FrameSplit {
  /** Complete frame blocks, blank-line terminated in the source. */
  blocks: string[];
  /** Trailing partial frame — prepend to the next chunk. */
  remainder: string;
}

/**
 * What a frame means for the lifetime of the stream.
 * - `open`         — more frames are expected.
 * - `terminal-ok`  — the run finished and finished well.
 * - `terminal-error` — the run finished badly; do NOT reconnect into it.
 */
export type FrameVerdict = "open" | "terminal-ok" | "terminal-error";

/** Every `type` the build stream is allowed to emit. */
export const BUILD_FRAME_TYPES = [
  "log",
  "phase",
  "progress",
  "complete",
  "end",
  "error",
  "started",
  "connected",
  "reconnected",
  "cancelled",
  "prompt",
  "service-status",
] as const;

/**
 * Split a buffer on frame boundaries.
 *
 * CRLF is normalised first. Without that, a producer (or any proxy) that emits
 * `\r\n\r\n` would never match the `\n\n` separator and NOT ONE frame would
 * ever be delivered — the stream would look alive and render nothing.
 */
export const splitFrameBlocks = (buffer: string): FrameSplit => {
  const normalised = buffer.replace(/\r\n/g, "\n");
  const parts = normalised.split("\n\n");
  const remainder = parts.pop() ?? "";
  return { blocks: parts, remainder };
};

/**
 * Parse one frame block into `{event, id, data}`.
 *
 * Returns `null` for heartbeats, blank blocks, and anything whose payload is
 * not JSON — a malformed frame is skipped, never thrown, because one bad line
 * from the server must not tear down a live build log.
 */
export const parseFrameBlock = (block: string): RawSSEFrame | null => {
  if (!block.trim()) return null;

  let event = "message";
  let id: string | undefined;
  let dataStr = "";

  for (const line of block.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith(":")) continue; // comment / heartbeat

    if (trimmed.startsWith("event:")) {
      event = trimmed.substring(6).trim();
    } else if (trimmed.startsWith("id:")) {
      id = trimmed.substring(3).trim();
    } else if (trimmed.startsWith("data:")) {
      const d = trimmed.substring(5).trim();
      dataStr = dataStr ? dataStr + "\n" + d : d;
    } else if (trimmed.startsWith("{")) {
      // Bare-JSON fallback for producers that skip the SSE framing entirely.
      dataStr = trimmed;
    }
  }

  if (!dataStr) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(dataStr);
  } catch {
    return null;
  }

  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return null;
  }

  const data = parsed as Record<string, unknown>;

  // Let the SSE envelope fill in what the payload omitted.
  if (!data.type && event !== "message") data.type = event;
  if (data.eventId === undefined && id !== undefined && /^\d+$/.test(id)) {
    data.eventId = Number(id);
  }

  return { event, id, data };
};

/**
 * Parse every complete frame in `buffer`, returning the incomplete tail.
 *
 * Callers keep `remainder` and prepend it to the next chunk; at stream close
 * they pass it back through with `flushFrames` so a producer that omitted the
 * final blank line cannot swallow the terminal frame.
 */
export const parseFrames = (
  buffer: string,
): { frames: RawSSEFrame[]; remainder: string } => {
  const { blocks, remainder } = splitFrameBlocks(buffer);
  const frames: RawSSEFrame[] = [];

  for (const block of blocks) {
    const frame = parseFrameBlock(block);
    if (frame) frames.push(frame);
  }

  return { frames, remainder };
};

/** Parse whatever is left in the buffer at stream close. */
export const flushFrames = (remainder: string): RawSSEFrame[] => {
  const frame = parseFrameBlock(remainder);
  return frame ? [frame] : [];
};

const FAILED_PHASES = new Set(["failed", "error"]);

/**
 * Decide what a single frame means for the stream's lifetime.
 *
 * `complete` is terminal REGARDLESS of the `success` flag. A `complete` frame
 * with the flag omitted violates the contract above, and the resolution here is
 * deliberately pessimistic: fall back to `exitCode` if there is one, otherwise
 * call it an error. The alternative — treating an unlabelled `complete` as a
 * success — would have the UI announce a deploy that nobody verified.
 */
export const classifyFrame = (data: Record<string, unknown>): FrameVerdict => {
  const type = typeof data.type === "string" ? data.type : undefined;
  const success = data.success;
  const exitCode = typeof data.exitCode === "number" ? data.exitCode : undefined;
  const hasError =
    (typeof data.error === "string" && data.error.length > 0) ||
    (typeof data.errorCode === "string" && data.errorCode.length > 0);

  if (type === "complete") {
    if (success === true) return "terminal-ok";
    if (success === false || hasError) return "terminal-error";
    if (exitCode !== undefined) return exitCode === 0 ? "terminal-ok" : "terminal-error";
    return "terminal-error"; // unlabelled `complete` — see the doc comment
  }

  if (type === "error") return "terminal-error";
  if (type === "cancelled") return "terminal-error"; // terminal, and not retryable
  if (type === "end") {
    return exitCode !== undefined && exitCode !== 0 ? "terminal-error" : "terminal-ok";
  }

  // Frames that carry a verdict without a terminal `type`.
  const phase = typeof data.phase === "string" ? data.phase : undefined;
  if (phase && FAILED_PHASES.has(phase)) return "terminal-error";

  const status = typeof data.status === "string" ? data.status : undefined;
  if (status && FAILED_PHASES.has(status) && hasError) return "terminal-error";

  return "open";
};

/**
 * Report contract violations in a frame. Non-throwing by design — this is a
 * divergence tripwire for the Python side, not a gate. Returns `[]` when clean.
 */
export const validateBuildFrame = (data: Record<string, unknown>): string[] => {
  const problems: string[] = [];
  const type = data.type;

  if (typeof type !== "string") {
    problems.push("frame has no string `type`");
  } else if (!(BUILD_FRAME_TYPES as readonly string[]).includes(type)) {
    problems.push(`unknown frame type "${type}"`);
  }

  if (type === "complete" && typeof data.success !== "boolean") {
    problems.push("`complete` frame is missing the required boolean `success`");
  }

  if (data.data !== undefined && typeof data.data !== "string") {
    problems.push("`data` must be a base64 string");
  }

  if (data.eventId !== undefined && typeof data.eventId !== "number") {
    problems.push("`eventId` must be a number");
  }

  if (data.step !== undefined && typeof data.step !== "string") {
    problems.push("`step` must be a string");
  }

  return problems;
};
