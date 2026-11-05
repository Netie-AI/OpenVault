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
const KEYS_MANAGED_BY_OPENVAULT = "keys_managed_by_openvault";

const CONSUMER_SUBSCRIPTION_MESSAGE =
  "Pooling a consumer subscription account (e.g. Claude Code, Codex/ChatGPT, Cursor, " +
  "GitHub Copilot, Antigravity, Kiro, Kimi Coding, Trae, Devin) is disabled in this " +
  "edition of FreeRoute. Connect this provider with a real API key instead.";
const SESSION_RELAY_MESSAGE =
  "Relaying a browser chat session (a cookie or session token pasted in place of an " +
  "API key, or an imported/pooled browser login) is disabled in this edition of " +
  "FreeRoute. Connect this provider with a real API key instead.";
const KEYS_MANAGED_BY_OPENVAULT_MESSAGE =
  "Provider keys are stored in OpenVault. Add them at http://127.0.0.1:3010/keys.";
const UPSTREAM_SERVICE_NOT_INCLUDED_MESSAGE =
  "This integration starts or adopts a separate upstream product that is not included in " +
  "this edition of FreeRoute.";

interface HardDisabledRule {
  /** Matches this prefix and everything under it (`/api/oauth` also matches `/api/oauth/codex/import`). */
  prefix: string;
  code:
    | typeof CONSUMER_SUBSCRIPTION
    | typeof SESSION_RELAY
    | typeof UPSTREAM_SERVICE_NOT_INCLUDED
    | typeof KEYS_MANAGED_BY_OPENVAULT;
  message: string;
  /** One-line note on what lives at this prefix, for the report / future readers. */
  reason: string;
}

// Checked in order; first match wins. Longest/most-specific prefixes first so
// a narrower carve-out (none needed today) could be added above a broader one.
const RULES: HardDisabledRule[] = [
  {
    prefix: "/authorize",
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason:
      "Trae SOLO OAuth loopback callback at the app root. It stored the query-string " +
      "credentials as a pooled Trae connection with no auth.",
  },
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
    prefix: "/api/providers/volcengine-plan",
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason:
      "Signs in to the Volcengine console, keeps the console cookie and CSRF token on the " +
      "connection, and writes the API key it reads back into the local store.",
  },
  {
    prefix: "/api/providers/command-code/auth",
    code: KEYS_MANAGED_BY_OPENVAULT,
    message: KEYS_MANAGED_BY_OPENVAULT_MESSAGE,
    reason:
      "Command Code key handoff: the callback stores the key the site hands back and " +
      "apply writes it onto a connection. Provider keys live only in OpenVault.",
  },
  {
    prefix: "/api/services/dario",
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason:
      "Managed Dario sidecar: installs, starts and runs Claude Pro/Max OAuth logins " +
      "(/admin/login/*) for a subscription pool. Includes install, start, stop, restart, " +
      "update, status, auto-start and the admin accounts, login and import routes.",
  },
  {
    prefix: "/api/services/cliproxy",
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason:
      "Managed CLIProxyAPI sidecar: multi-account subscription rotation with Claude Code " +
      "wire emulation. Includes install, start, stop, restart, update, status, " +
      "auto-start, provider-expose and accounts.",
  },
  {
    prefix: "/api/version-manager",
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason:
      "The version manager's only tool is the cliproxyapi binary (download, start, stop, " +
      "restart, release check), the same sidecar as /api/services/cliproxy.",
  },
  {
    prefix: "/api/services/openwa",
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason:
      "Managed open-wa sidecar: a paired WhatsApp Web login held in a headless Chromium " +
      "that runs a stealth plugin. Includes install, start, stop, restart, update, " +
      "status, auto-start and auto-restart-adopted.",
  },
  {
    prefix: "/api/services/9router",
    code: UPSTREAM_SERVICE_NOT_INCLUDED,
    message: UPSTREAM_SERVICE_NOT_INCLUDED_MESSAGE,
    reason:
      "Installs/starts/stops/adopts a separate upstream product (9router) as a managed " +
      "child service, a distinct product FreeRoute does not bundle or manage.",
  },
  {
    prefix: "/api/radar",
    code: UPSTREAM_SERVICE_NOT_INCLUDED,
    message:
      "Radar is a catalog, offers and referral feed run by the upstream project's own server. " +
      "That service is not included in this edition of FreeRoute.",
    reason:
      "Radar syncs a signed feed from the upstream operator's host and stores a supporter " +
      "key for it. Every route under it (catalog, intel, offers, referrals, settings, sync) " +
      "answers 501 whatever RADAR_ENABLED says.",
  },
];

// Per-connection sub-routes under /api/providers/{id}/ that exist only for a
// pooled subscription login or a captured browser session. The id segment
// varies, so these are matched by pattern instead of prefix.
interface HardDisabledPatternRule {
  pattern: RegExp;
  code: typeof CONSUMER_SUBSCRIPTION | typeof SESSION_RELAY;
  message: string;
  reason: string;
}

const PATTERN_RULES: HardDisabledPatternRule[] = [
  {
    pattern: /^\/dashboard\/providers\/services\/(dario|cliproxy|cliproxyapi)(\/|$)/,
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason:
      "Reverse-proxied embed UI of the Dario and CLIProxyAPI Claude-subscription sidecars.",
  },
  {
    pattern: /^\/api\/providers\/[^/]+\/(login|chatgpt-web-codex-doctor|refresh-token)(\/|$)/,
    code: SESSION_RELAY,
    message: SESSION_RELAY_MESSAGE,
    reason:
      "login launches a browser and stores the captured cookies or token on the " +
      "connection; refresh-token renews a kimi-web session; the doctor inspects a " +
      "chatgpt-web session.",
  },
  {
    pattern: /^\/api\/providers\/[^/]+\/(refresh|refresh-cursor|claude-auth|codex-auth)(\/|$)/,
    code: CONSUMER_SUBSCRIPTION,
    message: CONSUMER_SUBSCRIPTION_MESSAGE,
    reason:
      "Manual OAuth refresh, Cursor session renewal, and Claude Code or Codex CLI " +
      "auth-file export and apply for pooled subscription connections.",
  },
];

export const INVALID_PATH_ENCODING_CODE = "invalid_path_encoding";

export interface HardDisabledMatch {
  /** 501 for a disabled route family, 400 for a path whose encoding cannot be read. */
  status: 501 | 400;
  body: { error: { code: string; message: string } };
}

const MAX_DECODE_ROUNDS = 4;

/**
 * The path the router will actually serve for `pathname`: percent-escapes
 * decoded (repeatedly, so `%256f` cannot hide an `o`), backslashes read as
 * slashes, and empty, `.` and `..` segments resolved. Next.js decodes the path
 * before it picks a route handler, so `/api/%6fauth/codex` reaches the handler
 * for `/api/oauth/codex`; a table that only looks at the raw string misses it.
 *
 * Returns `null` when the path cannot be read safely: a malformed escape, a NUL
 * byte, or escapes still unwrapping after MAX_DECODE_ROUNDS rounds. The caller
 * answers 400 for those instead of guessing.
 */
export function normalizeRoutePath(pathname: string): string | null {
  let path = pathname;
  let settled = false;
  for (let round = 0; round < MAX_DECODE_ROUNDS; round += 1) {
    let decoded: string;
    try {
      decoded = decodeURIComponent(path);
    } catch {
      return null;
    }
    if (decoded === path) {
      settled = true;
      break;
    }
    path = decoded;
  }
  if (!settled) return null;
  if (path.includes("\0")) return null;

  const segments: string[] = [];
  for (const segment of path.replace(/\\/g, "/").split("/")) {
    if (segment === "" || segment === ".") continue;
    if (segment === "..") {
      segments.pop();
      continue;
    }
    segments.push(segment);
  }
  // Lowercased so a case variant of a disabled prefix cannot slip past (the
  // proxy matcher is case-insensitive for the same reason).
  return `/${segments.join("/")}`.toLowerCase();
}

function matchNormalized(path: string): HardDisabledMatch | null {
  for (const rule of RULES) {
    if (path === rule.prefix || path.startsWith(`${rule.prefix}/`)) {
      return { status: 501, body: { error: { code: rule.code, message: rule.message } } };
    }
  }
  for (const rule of PATTERN_RULES) {
    if (rule.pattern.test(path)) {
      return { status: 501, body: { error: { code: rule.code, message: rule.message } } };
    }
  }
  return null;
}

/**
 * Returns the response to send for `pathname`, or `null` if it isn't
 * hard-disabled: a 501 body for a disabled family, or a 400 when the path's
 * encoding cannot be read. Both the raw path and its normalized form are
 * checked, so neither an escaped nor a plain spelling gets through.
 */
export function matchHardDisabledRoute(pathname: string): HardDisabledMatch | null {
  const normalized = normalizeRoutePath(pathname);
  if (normalized === null) {
    return {
      status: 400,
      body: {
        error: {
          code: INVALID_PATH_ENCODING_CODE,
          message: "The request path has an invalid or over-encoded escape sequence.",
        },
      },
    };
  }
  return matchNormalized(pathname.toLowerCase()) ?? matchNormalized(normalized);
}
