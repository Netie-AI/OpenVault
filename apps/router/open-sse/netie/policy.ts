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
// The dashboard catalog's alias table (every section: apikey, oauth, web-cookie,
// search, audio-only, ...). It carries aliases that have no provider-registry row,
// e.g. "firefly" for adobe-firefly and "poe" for poe-web.
import { ALIAS_TO_ID } from "@/shared/constants/providers";

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
  "Checking for, downloading, or installing upstream releases is disabled in this " +
  "edition of FreeRoute. FreeRoute ships and updates as part of OpenVault.";
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

// `anthropic-compatible-cc-*` custom nodes exist to reach relay gateways "that only
// accept traffic which looks like the official Claude Code client": the Claude
// Code user agent, Stainless client headers, the real client's header order and
// the CCH billing signature (open-sse/services/claudeCodeCompatible.ts). That is
// client impersonation, so the whole family answers tls_fingerprint_stealth_disabled.
// A plain `anthropic-compatible-*` node (honest headers, its own x-api-key) stays.
const CLAUDE_CODE_COMPATIBLE_NODE_PREFIX = "anthropic-compatible-cc-";

function isClaudeCodeCompatibleNodeId(id: string): boolean {
  return id.startsWith(CLAUDE_CODE_COMPATIBLE_NODE_PREFIX);
}

function isWebCookieProviderId(id: string): boolean {
  return Object.prototype.hasOwnProperty.call(WEB_COOKIE_PROVIDERS, id);
}

// Providers whose registry row says `authType: "none"` because the
// credential never passes through this app, but whose executor still spends a
// human's own consumer login or depends on a browser-grade fingerprint. The
// registry fields cannot tell these apart, so they are listed by id, alias and
// executor name, and checked BEFORE any registry lookup.
const EXECUTOR_AUTH_OVERRIDES: Record<string, DisabledCode> = {
  // devin-cli-agentic.ts spawns `devin acp` on the operator's own Devin login
  // ("Authentication is owned exclusively by the official Devin CLI").
  "devin-cli-agentic": DISABLED.consumerSubscription,
  dva: DISABLED.consumerSubscription,
  // auggie.ts: "Auggie delegates auth entirely to the user's local `auggie
  // login` session", an Augment subscription.
  auggie: DISABLED.consumerSubscription,
  aug: DISABLED.consumerSubscription,
  // zcode.ts: DEFAULT_PROVIDER_ID "builtin:zai-coding-plan", "the local ZCode
  // profile owns auth", a Z.ai coding-plan subscription.
  zcode: DISABLED.consumerSubscription,
  zc: DISABLED.consumerSubscription,
  // codex-app-server drives the local `codex app-server`, which "OWNS and
  // self-refreshes its OpenAI auth (~/.codex/auth.json)": a ChatGPT
  // subscription login, the same pooling as the `codex` provider.
  "codex-app-server": DISABLED.consumerSubscription,
  cxa: DISABLED.consumerSubscription,
  // cloudflare-playground.ts: "the only gate is a browser-grade TLS
  // fingerprint on the WS upgrade", passed by driving headless Chromium.
  "cloudflare-playground": DISABLED.tlsStealth,
  cfp: DISABLED.tlsStealth,
};

function executorAuthOverride(...names: string[]): DisabledCode | null {
  for (const name of names) {
    if (name && Object.prototype.hasOwnProperty.call(EXECUTOR_AUTH_OVERRIDES, name)) {
      return EXECUTOR_AUTH_OVERRIDES[name];
    }
  }
  return null;
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

  const override = executorAuthOverride(id, executor);
  if (override) return override;
  if (isClaudeCodeCompatibleNodeId(id)) return DISABLED.tlsStealth;

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

// Aliases that the executor map or a media registry accepts but that no
// provider-registry row and no dashboard-catalog row resolves to their canonical
// id. Resolved here so a gate that sees only the alias (e.g. the key validator
// with provider "firefly") classifies it exactly like the canonical id. The walker
// tests in open-sse/netie/__tests__/policy.test.mjs fail if an alias is missing
// from every table and so classifies differently from its canonical id.
//
// Not listed on purpose: `v0`. The registry makes it the alias of the API-key
// provider `v0-vercel`, while the executor map points it at the browser-session
// `v0-vercel-web` executor. Classifying the spelling follows the registry (allowed);
// getExecutor() refuses the session executor because it checks the provider the
// loaded executor serves (open-sse/executors/index.ts).
const ALIAS_TO_CANONICAL: Record<string, string> = {
  // adobe-firefly: image/video/upscale registries and the executor map use "firefly".
  firefly: "adobe-firefly",
  // amazon-q: catalog alias "aq".
  aq: "amazon-q",
  // poe-web: catalog alias "poe".
  poe: "poe-web",
};

/**
 * Resolve a provider id or alias to its canonical id: the explicit alias table
 * above first, then the provider registry (`RegistryEntry.alias`), then the
 * dashboard catalog (`ALIAS_TO_ID`). Follows alias chains, lowercases and trims.
 * An unknown id comes back unchanged.
 */
export function resolveCanonicalProviderId(providerId: string | null | undefined): string {
  let current = String(providerId ?? "").trim().toLowerCase();
  for (let hop = 0; hop < 4 && current; hop += 1) {
    const next = (
      ALIAS_TO_CANONICAL[current] ??
      getRegistryEntry(current)?.id ??
      ALIAS_TO_ID[current] ??
      current
    )
      .trim()
      .toLowerCase();
    if (next === current) break;
    current = next;
  }
  return current;
}

function classifyResolvedId(id: string): DisabledCode | null {
  const override = executorAuthOverride(id);
  if (override) return override;
  if (isClaudeCodeCompatibleNodeId(id)) return DISABLED.tlsStealth;

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

/**
 * Classify by provider id or alias. The id is first resolved to its canonical
 * id (`resolveCanonicalProviderId`, which covers executor-map aliases, media
 * registry aliases, `RegistryEntry.alias` and the dashboard catalog aliases),
 * then classified through the live registry, the web-cookie catalog, the
 * orphan-alias table above and the `-web` name-shape heuristic. The spelling
 * given is classified too, and the stricter of the two wins, so an alias with no
 * registry row still cannot reach a pooling or scraping executor class.
 */
export function classifyProviderId(providerId: string | null | undefined): DisabledCode | null {
  if (!providerId) return null;
  const id = providerId.trim().toLowerCase();
  if (!id) return null;

  const canonical = resolveCanonicalProviderId(id);
  const byCanonical = classifyResolvedId(canonical);
  if (byCanonical) return byCanonical;
  return canonical === id ? null : classifyResolvedId(id);
}

/** Throws `NetieDisabledError` if `providerId` is hard-disabled; no-op otherwise. */
export function assertProviderAllowed(providerId: string | null | undefined): void {
  const code = classifyProviderId(providerId);
  if (code) throw new NetieDisabledError(code);
}

// Managed sidecar services that exist only to pool a consumer subscription
// login. dario.ts pools "the operator's own Claude Pro/Max subscription (Claude
// Code OAuth)" and rebuilds every request into Claude Code's wire shape;
// CLIProxyAPI does the same with multi-account rotation, CCH signing and uTLS.
// Installing, starting or supervising either one is disabled at the lowest
// layer (ServiceSupervisor, the installers, the version manager, the startup
// bootstrap) so no route, CLI command, startup hook or scheduler can reach it.
// The HTTP families are also blocked in `src/lib/netie/hardDisabledRoutes.ts`.
// 9router is a different, named case (`upstream_service_not_included`).
const DISABLED_SERVICE_TOOLS: ReadonlySet<string> = new Set(["dario", "cliproxy", "cliproxyapi"]);

/** True when `tool` names a managed sidecar that is hard-disabled in this edition. */
export function isServiceToolDisabled(tool: string | null | undefined): boolean {
  if (!tool) return false;
  return DISABLED_SERVICE_TOOLS.has(tool.trim().toLowerCase());
}

/** Throws `NetieDisabledError(consumer_subscription_pooling_disabled)` for a disabled sidecar. */
export function assertServiceToolAllowed(tool: string | null | undefined): void {
  if (isServiceToolDisabled(tool)) {
    throw new NetieDisabledError(DISABLED.consumerSubscription);
  }
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
