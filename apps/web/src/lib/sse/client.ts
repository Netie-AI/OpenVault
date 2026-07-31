/**
 * SSE client helpers — thin wrappers that open a stream against OpenVault's
 * FastAPI and hand raw chunks to a caller-supplied `onMessage`.
 *
 * Prefer `useSSEStream` in components: it owns frame reassembly, base64 log
 * decoding and terminal writes. These helpers exist for the non-React paths
 * (and to keep one place that knows the stream URLs).
 *
 * No `credentials: "include"` anywhere: the UI is served from :3010 and the API
 * from :5000, so every call is cross-origin. Sending credentials would force
 * the API into `Access-Control-Allow-Credentials` + an exact-origin echo, and
 * a desktop app on loopback gains nothing from it.
 */

import { OPENVAULT_API } from "@/lib/config";
import { classifyFrame, flushFrames, parseFrames, type RawSSEFrame } from "./frames";

export interface SSEClientOptions {
  onMessage: (data: string) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Error) => void;
  headers?: Record<string, string>;
}

/** A failure that must not be retried — the build already ended badly. */
export class NoRetryError extends Error {
  public readonly shouldRetry = false;

  constructor(message: string) {
    super(message);
    this.name = "NoRetryError";
  }
}

/** Live build/deploy logs for one Ship engine run. */
export const buildStreamUrl = (engineRunId: string): string =>
  `${OPENVAULT_API}/api/ship/engine/${encodeURIComponent(engineRunId)}/stream`;

/**
 * Attach to a running (or replayed) build stream.
 *
 * `lastEventId` lets the server skip frames the client already rendered after a
 * reconnect. The client dedups on `eventId` regardless, so passing it is an
 * optimisation, not a correctness requirement.
 */
export const connectToBuildLogs = async (
  engineRunId: string,
  options: SSEClientOptions,
  lastEventId?: number,
) => {
  const url =
    lastEventId === undefined
      ? buildStreamUrl(engineRunId)
      : `${buildStreamUrl(engineRunId)}?lastEventId=${lastEventId}`;

  return connectToSSE(url, options);
};

/** Generic SSE connection. */
export const connectToSSE = async (url: string, options: SSEClientOptions) => {
  const response = await fetch(url, {
    method: "GET",
    headers: {
      Accept: "text/event-stream",
      ...options.headers,
    },
  });

  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    options.onError?.(new Error(errorText));
    return { disconnect: () => {} };
  }

  return processSSEStream(response, options);
};

/** Human-readable reason for a terminal-error frame, for the NoRetryError. */
const terminalErrorMessage = (frame: RawSSEFrame | null): string => {
  const data = frame?.data;
  if (!data) return "Build ended with an error";

  const error = typeof data.error === "string" ? data.error : undefined;
  const message = typeof data.message === "string" ? data.message : undefined;
  if (error || message) return (error || message) as string;

  if (data.type === "cancelled") return "Build cancelled";
  if (data.type === "complete" && data.success === undefined) {
    // Contract violation, surfaced rather than papered over — see frames.ts.
    return "Build ended without reporting success or failure";
  }
  if (typeof data.exitCode === "number" && data.exitCode !== 0) {
    return `Process exited with code ${data.exitCode}`;
  }
  return "Build ended with an error";
};

const processSSEStream = async (response: Response, options: SSEClientOptions) => {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("No reader available");

  options.onConnect?.();

  const decoder = new TextDecoder();
  let sawTerminalError = false;
  let terminalFrame: RawSSEFrame | null = null;

  /**
   * Scan EVERY frame in the buffer, not just the first.
   *
   * One TCP chunk routinely carries several frames — a burst of `log` lines
   * followed by `complete` is the normal shape at the end of a build. Stopping
   * at frame #1 threw away exactly the frame that carries the verdict.
   *
   * `pending` holds a frame split across chunk boundaries; it is flushed at
   * stream close so a producer that omits the final blank line still gets its
   * terminal frame read.
   */
  let pending = "";
  const scan = (frames: RawSSEFrame[]) => {
    for (const frame of frames) {
      if (classifyFrame(frame.data) === "terminal-error") {
        sawTerminalError = true;
        terminalFrame = frame;
      }
    }
  };

  const readStream = async () => {
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        const { frames, remainder } = parseFrames(pending + chunk);
        pending = remainder;
        scan(frames);

        // Consumers reassemble the raw text themselves, so they get the chunk
        // verbatim — the buffering above is only for this layer's verdict.
        options.onMessage(chunk);
      }

      scan(flushFrames(pending));
      pending = "";

      if (sawTerminalError) {
        options.onError?.(new NoRetryError(terminalErrorMessage(terminalFrame)));
      } else {
        options.onDisconnect?.();
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        options.onError?.(err as Error);
      } else {
        options.onDisconnect?.();
      }
    }
  };

  void readStream();

  return {
    disconnect: () => {
      void reader.cancel();
    },
  };
};
