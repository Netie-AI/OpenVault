// Copyright (c) 2026 Netie AI. MIT.
//
// FreeRoute policy, the single place that decides which upstream provider
// "styles" this edition serves.
//
// FreeRoute is the API-key-only edition of the upstream router: it forwards a
// request to a provider using a real, user-supplied API key (or a provider
// that genuinely needs none). It never plays a human's own consumer
// subscription login (Claude Pro/Max via Claude Code, ChatGPT Plus via Codex,
// Cursor, GitHub Copilot, Antigravity, Kiro, Kimi Coding, Trae, Devin, ...)
// across many requests, and it never replays a browser chat session's cookies
// or session tokens (chatgpt.com, claude.ai, gemini.google.com, grok.com,
// chat.deepseek.com, M365 Copilot, duck.ai, huggingchat, lmarena, ...). Both
// are upstream ToS-adjacent account-pooling features that do not belong in a
// product that also brokers real API keys through OpenVault's KeyVault, see
// PRODUCT_ROLES.md.
//
// classifyProvider() is the ONLY place that tells the two apart from a
// provider registry entry's own fields (authType/authHeader/executor/id), so
// every choke point (executor dispatch, OAuth routes, dashboard listings)
// stays in sync with the registry instead of carrying its own copy of a
// hardcoded id list.

import { getRegistryEntry } from "../config/providerRegistry.ts";
// Canonical, hand-curated catalog of every provider whose "apikey" field is
// actually a browser cookie / session token a human pasted out of their own
// signed-in tab (chatgpt.com, claude.ai, poe.com, prompt.ql.app, uncensored.com,
// ...), see that file's own JSDoc. More authoritative than a name-shape guess
// for providers whose registry entry doesn't say `authHeader: "cookie"` at all
// (e.g. promptql, maxai, uc, inner-ai all read a bearer/none-shaped registry
// entry but are, per their own dashboard copy, a personal sign-in relayed or
// refreshed on the operator's behalf).
import { WEB_COOKIE_PROVIDERS } from "@/shared/constants/providers/web-cookie";

/** Canonical 501 error codes for every hard-disabled feature family. */
export const DISABLED = {
  consumerSubscription: "consumer_subscription_pooling_disabled",
  sessionRelay: "consumer_session_relay_disabled",
  tlsStealth: "tls_fingerprint_stealth_disabled",
} as const;

export type DisabledCode = (typeof DISABLED)[keyof typeof DISABLED];

const DISABLED_MESSAGES: Record<DisabledCode, string> = {
  [DISABLED.consumerSubscription]:
    "Pooling a consumer subscription account (e.g. Claude Code, Codex/ChatGPT, Cursor, " +
    "GitHub Copilot, Antigravity, Kiro, Kimi Coding, Trae, Devin) is disabled in this " +
    "edition of FreeRoute. Connect this provider with a real API key instead.",
  [DISABLED.sessionRelay]:
    "Relaying a browser chat session (a cookie or session token pasted in place of an " +
    "API key, or an imported/pooled browser login) is disabled in this edition of " +
    "FreeRoute. Connect this provider with a real API key instead.",
  [DISABLED.tlsStealth]:
    "TLS and browser fingerprint impersonation (stealth transports that mimic a " +
    "specific client's JA3/HTTP fingerprint to evade bot detection) is disabled in " +
    "this edition of FreeRoute.",
};

export function disabledMessage(code: DisabledCode): string {
  return DISABLED_MESSAGES[code];
}

/** Thrown by choke points that gate on `classifyProvider`/`classifyProviderId`. */
export class NetieDisabledError extends Error {
  readonly status = 501 as const;
  readonly code: DisabledCode;

  constructor(code: DisabledCode, message?: string) {
    super(message ?? DISABLED_MESSAGES[code]);
    this.name = "NetieDisabledError";
    this.code = code;
  }
}

export function isNetieDisabledError(error: unknown): error is NetieDisabledError {
  return error instanceof NetieDisabledError;
}

/** Body shape mandated by the brief for every hard-disabled feature (HTTP 501). */
export interface DisabledErrorBody {
  error: { code: DisabledCode | string; message: string };
}

export function disabledErrorBody(code: DisabledCode, message?: string): DisabledErrorBody {
  return { error: { code, message: message ?? DISABLED_MESSAGES[code] } };
}

/**
 * Render the named 501 as a Fetch API `Response`, usable directly from a
 * Next.js route handler or from `NextResponse.json(body, {status})`-style
 * call sites (pass `.status`/the body through instead if the caller already
 * has its own response helper).
 */
export function renderDisabled(code: DisabledCode, message?: string): Response {
  return new Response(JSON.stringify(disabledErrorBody(code, message)), {
    status: 501,
    headers: { "content-type": "application/json" },
  });
}

/**
 * Generic named-501 renderer for hard-disabled features that aren't a
 * provider-classification bucket (`DisabledCode`) or the key-storage 501 -
 * e.g. `upstream_updates_disabled`, `upstream_service_not_included`. Same
 * `{error:{code,message}}` shape as every other named 501 in this module.
 */
export function renderNamed501(code: string, message: string): Response {
  return new Response(JSON.stringify({ error: { code, message } }), {
    status: 501,
    headers: { "content-type": "application/json" },
  });
}

export const UPSTREAM_UPDATES_DISABLED_CODE = "upstream_updates_disabled";
export const UPSTREAM_UPDATES_DISABLED_MESSAGE =
  "Checking for, downloading, or installing upstream OmniRoute releases is disabled in " +
  "this edition of FreeRoute. FreeRoute ships and updates as part of OpenVault.";
export function renderUpstreamUpdatesDisabled(): Response {
  return renderNamed501(UPSTREAM_UPDATES_DISABLED_CODE, UPSTREAM_UPDATES_DISABLED_MESSAGE);
}

export const UPSTREAM_SERVICE_NOT_INCLUDED_CODE = "upstream_service_not_included";
export const UPSTREAM_SERVICE_NOT_INCLUDED_MESSAGE =
  "This integration starts or adopts a separate upstream product that is not included in " +
  "this edition of FreeRoute.";
export function renderUpstreamServiceNotIncluded(): Response {
  return renderNamed501(
    UPSTREAM_SERVICE_NOT_INCLUDED_CODE,
    UPSTREAM_SERVICE_NOT_INCLUDED_MESSAGE
  );
}

/**
 * Extra: the fixed 501 for any route/UI that would persist a provider API key
 * into FreeRoute's own store (brief §4/keyvault.ts) rather than reading it
 * from OpenVault. Not a `DisabledCode` bucket (it is not about which
 * *provider* is disabled) but shares the same 501 JSON shape.
 */
export const KEYS_MANAGED_BY_OPENVAULT_CODE = "keys_managed_by_openvault";
export function renderKeysManagedByOpenVault(
  message = "Provider keys are stored in OpenVault. Add them at http://127.0.0.1:3010/keys.",
): Response {
  return new Response(
    JSON.stringify({ error: { code: KEYS_MANAGED_BY_OPENVAULT_CODE, message } }),
    { status: 501, headers: { "content-type": "application/json" } }
  );
}

/** Same 501, worded for the ROUTER'S OWN inbound-client keys (not a provider key). */
export function renderClientKeysManagedByOpenVault(): Response {
  return renderKeysManagedByOpenVault(
    "FreeRoute's own client API keys are issued by OpenVault. Create or rotate one at " +
      "http://127.0.0.1:3010/keys (OpenVault mints it via POST /api/apikeys)."
  );
}

/** Minimal shape `classifyProvider` needs, a subset of `RegistryEntry`. */
export interface ClassifiableProvider {
  id?: string | null;
  authType?: string | null;
  authHeader?: string | null;
  executor?: string | null;
}

// Registry ids/executors that end in "-web" are, by construction in this
// codebase, browser-tab scrapers (chatgpt-web, claude-web, gemini-web,
// grok-web, deepseek-web, copilot-m365-web, duckduckgo-web, t3-web, ...).
// Matched regardless of the declared authHeader: several of them (e.g.
// deepseek-web, zai-web, adapta-web, duckduckgo-web) take a `bearer`/`none`
// header at the registry level but the "apikey" they carry is actually a
// session token/cookie lifted from the site's own web app, not a real
// provider-issued API key.
const WEB_SCRAPER_SUFFIX = /-web$/;

function isWebCookieProviderId(id: string): boolean {
  return Object.prototype.hasOwnProperty.call(WEB_COOKIE_PROVIDERS, id);
}

/**
 * Classify a provider registry entry (or an entry-shaped object, e.g. a row
 * read back from a DB) into a disabled-feature bucket, or `null` if the
 * provider stays allowed (real API key, `none`, or `optional` auth).
 */
export function classifyProvider(entry: ClassifiableProvider | null | undefined): DisabledCode | null {
  if (!entry) return null;
  const authType = String(entry.authType ?? "").toLowerCase();
  const authHeader = String(entry.authHeader ?? "").toLowerCase();
  const id = String(entry.id ?? "").toLowerCase();
  const executor = String(entry.executor ?? "").toLowerCase();

  // Subscription accounts authenticated through a real OAuth device/PKCE/
  // browser-callback flow: Claude Code, Codex, Cursor, GitHub/GHE Copilot,
  // Antigravity, Kiro/Amazon Q, Cline/ClinePass, Kilocode, Openference, Trae,
  // Devin (desktop/CLI), CodeBuddy CN, Kimi Coding, GitLab Duo, Zed Hosted,
  // xAI's "xai-oauth" Grok Build variant, ...
  if (authType === "oauth") return DISABLED.consumerSubscription;

  // Browser-session scrapers: the registry marks the credential as a pasted
  // cookie, the id/executor itself names it a *-web scraper, or the curated
  // WEB_COOKIE_PROVIDERS catalog (src/shared/constants/providers/web-cookie.ts)
  // says the "apikey" it takes is really a browser session artifact.
  if (
    authHeader === "cookie" ||
    WEB_SCRAPER_SUFFIX.test(id) ||
    WEB_SCRAPER_SUFFIX.test(executor) ||
    (id && isWebCookieProviderId(id))
  ) {
    return DISABLED.sessionRelay;
  }

  return null;
}

// Executor-dispatch aliases (open-sse/executors/index.ts) that have NO
// corresponding entry in the provider REGISTRY at all, so `getRegistryEntry`
// can't classify them from registry fields, but that construct an executor
// class which is, by its own source comments, a subscription-account pooler
// or a browser-session scraper. Verified individually against
// open-sse/executors/*.ts; see the FreeRoute report for the file:line each
// one was read from.
const ORPHAN_ALIAS_OVERRIDES: Record<string, DisabledCode> = {
  // Kiro executor reused for AWS's own OAuth device flow (Amazon Q Developer
  // subscription seat), no registry entry, dispatched only via the alias.
  "amazon-q": DISABLED.consumerSubscription,
  // Alias for devin-cli (registry authType "oauth").
  devin: DISABLED.consumerSubscription,
  // dario.ts: "pools ... the operator's own Claude Pro/Max subscription
  // (Claude Code OAuth)" per its own header comment.
  dario: DISABLED.consumerSubscription,
  dr: DISABLED.consumerSubscription,
  // gitlab.ts dispatches through GitLab's own OAuth endpoints; only the
  // "-duo" registry entry exists, this bare alias is the same OAuth pooling
  // without a registry row.
  gitlab: DISABLED.consumerSubscription,
  // Proxies to a local CLIProxyAPI instance whose own docstring says it does
  // "full emulation (CCH signing, billing header, ..., uTLS, multi-account
  // rotation, device profile learning)" of Claude Code/CLI subscription
  // accounts, pooling AND TLS fingerprint impersonation in one hop.
  cliproxyapi: DISABLED.consumerSubscription,
  cpa: DISABLED.consumerSubscription,
  // Alias for copilot-web (registry authHeader "cookie").
  copilot: DISABLED.sessionRelay,
  // Alias for claude-web.
  "cw-web": DISABLED.sessionRelay,
  // Alias for huggingchat (registry authHeader "cookie").
  hc: DISABLED.sessionRelay,
  // Alias for venice-web (itself only reachable via WEB_COOKIE_PROVIDERS below,
  // no registry row under either spelling).
  ven: DISABLED.sessionRelay,
};

/**
 * Classify by provider id or alias, resolving through the live registry
 * first (covers every canonical id and every `RegistryEntry.alias`, which is
 * how the dashboard/model-routing layer actually addresses providers, see
 * `getRegistryEntry`). Falls back to the small orphan-alias table above, then
 * to the `-web` name-shape heuristic, so an executor-only alias with no
 * registry row still can't reach a pooling/scraping executor class.
 */
export function classifyProviderId(providerId: string | null | undefined): DisabledCode | null {
  if (!providerId) return null;
  const id = providerId.trim().toLowerCase();
  if (!id) return null;

  const entry = getRegistryEntry(id);
  if (entry) return classifyProvider(entry);

  // No registry row under this exact spelling (e.g. poe-web/v0-vercel-web/
  // adobe-firefly, whose registry entry, if any, lives under a different
  // id): the curated web-cookie catalog still knows it by this id.
  if (isWebCookieProviderId(id)) return DISABLED.sessionRelay;
  if (id in ORPHAN_ALIAS_OVERRIDES) return ORPHAN_ALIAS_OVERRIDES[id];
  if (WEB_SCRAPER_SUFFIX.test(id)) return DISABLED.sessionRelay;
  return null;
}

/** Throws `NetieDisabledError` if `providerId` is hard-disabled; no-op otherwise. */
export function assertProviderAllowed(providerId: string | null | undefined): void {
  const code = classifyProviderId(providerId);
  if (code) throw new NetieDisabledError(code);
}

/**
 * Same as `assertProviderAllowed`, but takes a model id that may be
 * "<providerAliasOrId>/<model>" (mirrors
 * `assertRuntimeModelProviderAvailable`'s prefix extraction in
 * `src/shared/constants/providerRetirement.ts`), the shape callers get from
 * a chat-completions request body's `model` field.
 */
export function assertModelProviderAllowed(modelId: unknown): void {
  if (typeof modelId !== "string") return;
  const slashIndex = modelId.indexOf("/");
  if (slashIndex <= 0) return;
  assertProviderAllowed(modelId.slice(0, slashIndex));
}

/** True when a provider (registry entry or bare id/alias) should be hidden from pickers. */
export function isProviderHidden(entryOrId: ClassifiableProvider | string | null | undefined): boolean {
  if (typeof entryOrId === "string" || entryOrId == null) {
    return classifyProviderId(entryOrId as string | null) !== null;
  }
  return classifyProvider(entryOrId) !== null;
}
