// Copyright (c) 2026 Netie AI. MIT.
//
// FreeRoute, path-prefix hard-disable table for whole route families that
// exist only to pool a consumer subscription account or relay a browser chat
// session, checked once in `src/proxy.ts` (the Next.js request gate) before
// any of these routes' own handlers run.
//
// This intentionally does NOT import `open-sse/netie/policy.ts`: that module
// pulls in the full ~250-file provider registry, which there is no reason to
// load into the proxy's request path for a check that is a plain path-prefix
// match here. The DISABLED codes are duplicated as literals; keep them in
// sync with `open-sse/netie/policy.ts`'s `DISABLED` map and its
// `UPSTREAM_SERVICE_NOT_INCLUDED_CODE`/`UPSTREAM_UPDATES_DISABLED_CODE`.
//
// Per-provider (not whole-route-family) disabling, e.g. a chat completion
// naming an oauth-classified model, or `getExecutor()` dispatch, is handled
// separately by `open-sse/netie/policy.ts`, which DOES need the registry.

const CONSUMER_SUBSCRIPTION = "consumer_subscription_pooling_disabled";
const SESSION_RELAY = "consumer_session_relay_disabled";
const UPSTREAM_SERVICE_NOT_INCLUDED = "upstream_service_not_included";

const CONSUMER_SUBSCRIPTION_MESSAGE =
  "Pooling a consumer subscription account (e.g. Claude Code, Codex/ChatGPT, Cursor, " +
  "GitHub Copilot, Antigravity, Kiro, Kimi Coding, Trae, Devin) is disabled in this " +
  "edition of FreeRoute. Connect this provider with a real API key instead.";
const SESSION_RELAY_MESSAGE =
  "Relaying a browser chat session (a cookie or session token pasted in place of an " +
  "API key, or an imported/pooled browser login) is disabled in this edition of " +
  "FreeRoute. Connect this provider with a real API key instead.";
const UPSTREAM_SERVICE_NOT_INCLUDED_MESSAGE =
  "This integration starts or adopts a separate upstream product that is not included in " +
  "this edition of FreeRoute.";

interface HardDisabledRule {
  /** Matches this prefix and everything under it (`/api/oauth` also matches `/api/oauth/codex/import`). */
  prefix: string;
  code: typeof CONSUMER_SUBSCRIPTION | typeof SESSION_RELAY | typeof UPSTREAM_SERVICE_NOT_INCLUDED;
  message: string;
  /** One-line note on what lives at this prefix, for the report / future readers. */
  reason: string;
}

// Checked in order; first match wins. Longest/most-specific prefixes first so
// a narrower carve-out (none needed today) could be added above a broader one.
const RULES: HardDisabledRule[] = [
  {
    prefix: "/api/oauth",
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason:
      "Every action here (authorize/device-code/exchange/poll/import-token/paste-credentials, " +
      "plus the codex/kiro/cursor/trae/cliproxy-import literal sub-routes) starts or completes " +
      "an OAuth device/PKCE flow or imports an existing CLI/keychain/browser credential.",
  },
  {
    prefix: "/api/codex",
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason: "codex/connect/[token]: public completion endpoint for the Codex OAuth device flow.",
  },
  {
    prefix: "/api/cursor-cli",
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason: "Proxies the Cursor CLI wire protocol using a pooled Cursor subscription account.",
  },
  {
    prefix: "/api/providers/agy-auth",
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason: "Imports an existing Antigravity CLI credential (zip/local file) as a pooled account.",
  },
  {
    prefix: "/api/providers/claude-auth",
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason: "Imports an existing Claude Code CLI credential as a pooled account.",
  },
  {
    prefix: "/api/providers/codex-auth",
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason: "Imports an existing Codex CLI credential as a pooled account.",
  },
  {
    prefix: "/api/providers/zed",
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason: "Discovers/imports a Zed IDE keychain sign-in as a pooled account.",
  },
  {
    prefix: "/api/copilot",
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason: "copilot/chat dispatches to CopilotWebExecutor, a browser-cookie relay.",
  },
  {
    prefix: "/api/providers/bulk-web-session",
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason: "Bulk-imports browser cookies/session tokens for the *-web provider family.",
  },
  {
    prefix: "/api/providers/web-session-contract",
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason: "Describes/validates the cookie/session-token shape a *-web provider expects.",
  },
  {
    prefix: "/api/vnc-session",
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason: "Drives a headless/VNC browser session to sign in and capture a web session.",
  },
  {
    prefix: "/api/session-pools",
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason: "Manages pooled browser chat sessions (packages/browser-pool) across accounts.",
  },
  {
    prefix: "/api/cli-tools/antigravity-mitm",
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason: "Controls the MITM subsystem (src/mitm) that intercepts IDE traffic to reuse a login.",
  },
  {
    prefix: "/api/settings/mitm",
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason: "MITM subsystem settings (src/mitm), see above.",
  },
  {
    prefix: "/api/tools/agent-bridge",
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason: "\"Agent Bridge\": the MITM subsystem's dashboard-facing name (cert, tproxy, DNS, per-IDE-agent state).",
  },
  {
    prefix: "/api/tools/traffic-inspector",
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason: "Traffic capture/replay for the MITM subsystem.",
  },
  {
    prefix: "/api/services/9router",
    code: UPSTREAM_SERVICE_NOT_INCLUDED,
    message: UPSTREAM_SERVICE_NOT_INCLUDED_MESSAGE,
    reason:
      "Installs/starts/stops/adopts a separate upstream product (9router) as a managed " +
      "child service, a distinct product FreeRoute does not bundle or manage.",
  },
];

export interface HardDisabledMatch {
  status: 501;
  body: { error: { code: string; message: string } };
}

/** Returns the 501 body to send for `pathname`, or `null` if it isn't hard-disabled. */
export function matchHardDisabledRoute(pathname: string): HardDisabledMatch | null {
  for (const rule of RULES) {
    if (pathname === rule.prefix || pathname.startsWith(`${rule.prefix}/`)) {
      return { status: 501, body: { error: { code: rule.code, message: rule.message } } };
    }
  }
  return null;
}
