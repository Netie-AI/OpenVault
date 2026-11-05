import { randomUUID } from "crypto";
import { setUserAgentHeader } from "../executors/base.ts";
import {
  resolveOpencodeSessionIdentity,
  type OpencodeSessionBody,
} from "./opencodeSessionIdentity.ts";

// FreeRoute does not present itself as the OpenCode CLI. Earlier builds filled in
// an `opencode/<version>` User-Agent, `x-opencode-client`, `x-opencode-project` and
// forged `ses_`/`msg_` ids on every request so the free tier's client-only check
// let a datacenter caller through. That is client impersonation
// (tls_fingerprint_stealth_disabled), so it is gone: only headers a real OpenCode
// client sent itself are forwarded, and the upstream answers whatever it answers.

/**
 * The session id the caller supplied, if any.
 *
 * Only a client-supplied value joins two requests of one conversation: a synthesized one
 * is derived from the body, and the body of a build request and of the title request that
 * follows it differ — including in their tool list, which is the very thing being joined.
 */
export function clientSuppliedOpencodeSession(
  clientHeaders: Record<string, string> | null | undefined,
  body?: unknown
): string | undefined {
  return resolveOpencodeSessionIdentity(clientHeaders, body);
}

/**
 * Header keys that are forwarded from the client to the upstream provider.
 * Used by both OpencodeExecutor and DefaultExecutor.
 */
const OPENCODE_HEADER_KEYS = [
  "x-opencode-session",
  "x-opencode-request",
  "x-opencode-project",
  "x-opencode-client",
] as const;

/**
 * Common agent-metadata headers used by non-OpenCode clients (custom agents/
 * providers) for upstream request tracking and attribution. Forwarded the same
 * way as the x-opencode-* set: case-insensitive lookup, client value wins.
 * Added for 9router#2413 — these were previously dropped for every client
 * outside the OpenCode allowlist.
 */
const AGENT_METADATA_HEADER_KEYS = ["x-session-id", "x-title"] as const;

/**
 * Case-insensitive lookup for a header in a headers record.
 */
function findHeader(headers: Record<string, string>, name: string): string | undefined {
  return Object.entries(headers).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1];
}

/**
 * Forward OpenCode client request metadata headers to the upstream provider.
 *
 * Shared logic used by OpencodeExecutor and DefaultExecutor:
 * 1. Forwards User-Agent from clientHeaders via `setUserAgentHeader()`
 * 2. Forwards x-opencode-session, x-opencode-request, x-opencode-project,
 *    x-opencode-client headers (case-insensitive match)
 * 3. Forwards x-session-id, x-title agent-metadata headers (case-insensitive
 *    match) — common conventions used by non-OpenCode agent clients (9router#2413)
 *
 * @param headers - The outbound headers record to mutate
 * @param clientHeaders - The client-provided headers to forward from
 * @param options.synthesizeRequestId - When true (OpencodeExecutor only), maps
 *   x-session-affinity / x-session-id to x-opencode-session when the latter is
 *   missing, and synthesizes a UUID for x-opencode-request if also missing.
 * @param options.sessionBody - Request body fields used to generate a
 *   conversation-stable session fingerprint (model, system, messages or input, tools).
 *   When provided, x-opencode-session is a deterministic hash instead of a random
 *   UUID, so upstream prompt caching hits across requests in the same conversation.
 */
export function forwardOpencodeClientHeaders(
  headers: Record<string, string>,
  clientHeaders: Record<string, string>,
  options?: {
    synthesizeRequestId?: boolean;
    sessionBody?: OpencodeSessionBody;
  }
): void {
  // 1. Forward User-Agent
  const clientUA = clientHeaders["User-Agent"] || clientHeaders["user-agent"];
  if (clientUA) {
    setUserAgentHeader(headers, clientUA);
  }

  // 2. Forward x-opencode-* metadata headers
  for (const headerName of OPENCODE_HEADER_KEYS) {
    const value = findHeader(clientHeaders, headerName);
    if (value) {
      headers[headerName] = value;
    }
  }

  // 2b. Forward agent-metadata headers (x-session-id, x-title) — 9router#2413
  for (const headerName of AGENT_METADATA_HEADER_KEYS) {
    const value = findHeader(clientHeaders, headerName);
    if (value) {
      headers[headerName] = value;
    }
  }

  // 3. OpencodeExecutor-only: synthesize session/request id from fallback headers
  if (options?.synthesizeRequestId) {
    applySessionFallback(headers, clientHeaders, options.sessionBody);
  }
}

/** Fill missing session/request identity without changing the CLI synthesis policy. */
function applySessionFallback(
  headers: Record<string, string>,
  clientHeaders: Record<string, string>,
  sessionBody?: OpencodeSessionBody
): void {
  if (headers["x-opencode-session"]) return;
  const sessionAffinity = resolveOpencodeSessionIdentity(clientHeaders, sessionBody);
  if (!sessionAffinity) return;
  // The caller's own identity, carried as-is.
  headers["x-opencode-session"] = sessionAffinity;
  headers["x-opencode-request"] ||= randomUUID();
}
