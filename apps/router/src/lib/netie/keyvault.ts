// Copyright (c) 2026 Netie AI. MIT.
//
// FreeRoute KeyVault client, the ONLY source of provider API keys, and the
// verifier for OpenVault-issued inbound client tokens (see verifyClientToken).
//
// FreeRoute never stores a provider's real API key in its own DB, files, or
// env (see src/app/api/providers/route.ts and [id]/route.ts, which return the
// `keys_managed_by_openvault` 501 instead of persisting one). Every outgoing
// request instead resolves its credential here, live, from OpenVault:
//
//   GET  {OPENVAULT_URL}/api/keys                        -> the account's keys (no plaintext)
//   GET  {OPENVAULT_URL}/api/keys/{id}/secret             -> {id, secret} (audited, loopback only)
//
// Every call also sends X-OpenVault-Admin. OpenVault requires it on every
// route under /api/keys, /api/keyvault, /api/apikeys, /api/secrets,
// /api/vault and /keys, from loopback too. The value is the token in the
// mode-0600 file OpenVault creates on first start:
//
//   $OPENVAULT_ADMIN_TOKEN_PATH, else $OPENVAULT_HOME/admin_token,
//   else ~/.openvault/admin_token
//
// The file is read per OpenVault call (the 60s caches below bound how often
// that happens). The token is never logged and never put in an error message;
// only the path is.
//
// Plaintext secrets are cached in memory only, for at most CACHE_TTL_MS, and
// are never written to disk or logged. Every OpenVault failure throws an
// `OpenVaultUnreachableError` (HTTP 503) whose `code` names the cause:
//
//   openvault_keyvault_unreachable      OpenVault down, timed out, or other error
//   openvault_admin_token_unavailable   admin token file missing or empty
//   openvault_admin_token_rejected      OpenVault answered 401 to the token
//   openvault_keyvault_sealed           secret reveal refused because the vault is sealed
//
// Callers must not fall back to a local store.
//
// Choke point: src/sse/services/auth.ts `materializeConnection()` calls
// `resolveProviderApiKey()` here in place of reading `connection.apiKey` for
// every `authType: "apikey"` connection.

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const DEFAULT_OPENVAULT_URL = "http://127.0.0.1:5000";
const CACHE_TTL_MS = 60_000;
const REQUEST_TIMEOUT_MS = 5_000;
const REVEAL_HEADER = "X-OpenVault-Reveal";
const REVEAL_HEADER_VALUE = "intentional";
const ADMIN_HEADER = "X-OpenVault-Admin";

export const KEYS_MANAGED_BY_OPENVAULT_CODE = "keys_managed_by_openvault";
export const OPENVAULT_KEYVAULT_UNREACHABLE_CODE = "openvault_keyvault_unreachable";
export const OPENVAULT_ADMIN_TOKEN_UNAVAILABLE_CODE = "openvault_admin_token_unavailable";
export const OPENVAULT_ADMIN_TOKEN_REJECTED_CODE = "openvault_admin_token_rejected";
export const OPENVAULT_KEYVAULT_SEALED_CODE = "openvault_keyvault_sealed";

export type OpenVaultErrorCode =
  | typeof OPENVAULT_KEYVAULT_UNREACHABLE_CODE
  | typeof OPENVAULT_ADMIN_TOKEN_UNAVAILABLE_CODE
  | typeof OPENVAULT_ADMIN_TOKEN_REJECTED_CODE
  | typeof OPENVAULT_KEYVAULT_SEALED_CODE;

function openVaultBaseUrl(): string {
  const raw = process.env.OPENVAULT_URL?.trim() || DEFAULT_OPENVAULT_URL;
  return raw.replace(/\/+$/, "");
}

function expandHome(path: string): string {
  if (path === "~") return homedir();
  if (path.startsWith("~/")) return join(homedir(), path.slice(2));
  return path;
}

/** Same resolution order as OpenVault's admin_token_path(). */
export function resolveAdminTokenPath(): string {
  const override = process.env.OPENVAULT_ADMIN_TOKEN_PATH?.trim();
  if (override) return expandHome(override);
  const home = process.env.OPENVAULT_HOME?.trim();
  if (home) return join(expandHome(home), "admin_token");
  return join(homedir(), ".openvault", "admin_token");
}

/** Thrown whenever OpenVault cannot be reached or answers with an error. Never swallow this into a silent fallback. */
export class OpenVaultUnreachableError extends Error {
  readonly status = 503 as const;
  readonly code: OpenVaultErrorCode;
  constructor(message?: string, code: OpenVaultErrorCode = OPENVAULT_KEYVAULT_UNREACHABLE_CODE) {
    super(message ?? `OpenVault KeyVault is unreachable at ${openVaultBaseUrl()}.`);
    this.name = "OpenVaultUnreachableError";
    this.code = code;
  }
}

/** The admin token file is missing, unreadable or empty. */
export class OpenVaultAdminTokenUnavailableError extends OpenVaultUnreachableError {
  readonly path: string;
  constructor(path: string, reason: string) {
    super(
      `OpenVault admin token not available at ${path} (${reason}). OpenVault creates this file ` +
        `on first start. Start OpenVault once, or set OPENVAULT_ADMIN_TOKEN_PATH to the file it created.`,
      OPENVAULT_ADMIN_TOKEN_UNAVAILABLE_CODE
    );
    this.name = "OpenVaultAdminTokenUnavailableError";
    this.path = path;
  }
}

/** OpenVault answered 401 to the admin token we sent. */
export class OpenVaultAdminTokenRejectedError extends OpenVaultUnreachableError {
  readonly path: string;
  constructor(path: string, route: string) {
    super(
      `OpenVault rejected the admin token read from ${path} (401 on ${route}). ` +
        `Typical cause: OPENVAULT_HOME or OPENVAULT_ADMIN_TOKEN_PATH points at a different vault ` +
        `than the one running at ${openVaultBaseUrl()}.`,
      OPENVAULT_ADMIN_TOKEN_REJECTED_CODE
    );
    this.name = "OpenVaultAdminTokenRejectedError";
    this.path = path;
  }
}

/** OpenVault refused a secret reveal because the vault is sealed. */
export class OpenVaultKeyVaultSealedError extends OpenVaultUnreachableError {
  constructor(route: string) {
    super(
      `OpenVault KeyVault at ${openVaultBaseUrl()} is sealed (403 on ${route}). ` +
        `Unseal it in OpenVault, then retry.`,
      OPENVAULT_KEYVAULT_SEALED_CODE
    );
    this.name = "OpenVaultKeyVaultSealedError";
  }
}

/**
 * True for any KeyVault error, including one that crossed a module boundary
 * (duck-typed on name and code, so a second copy of this module still matches).
 */
export function isOpenVaultKeyVaultError(error: unknown): error is OpenVaultUnreachableError {
  if (error instanceof OpenVaultUnreachableError) return true;
  if (!error || typeof error !== "object") return false;
  const e = error as { status?: unknown; code?: unknown };
  return (
    e.status === 503 &&
    typeof e.code === "string" &&
    (e.code === OPENVAULT_KEYVAULT_UNREACHABLE_CODE ||
      e.code === OPENVAULT_ADMIN_TOKEN_UNAVAILABLE_CODE ||
      e.code === OPENVAULT_ADMIN_TOKEN_REJECTED_CODE ||
      e.code === OPENVAULT_KEYVAULT_SEALED_CODE)
  );
}

/** JSON 503 a route returns for a KeyVault error. The body carries the specific code. */
export function openVaultErrorResponse(
  error: OpenVaultUnreachableError,
  headers: Record<string, string> = {}
): Response {
  return new Response(
    JSON.stringify({
      error: { code: error.code, type: error.code, message: error.message },
    }),
    { status: error.status, headers: { ...headers, "content-type": "application/json" } }
  );
}

/** Read the admin token for one OpenVault call. Throws OpenVaultAdminTokenUnavailableError. */
function readAdminToken(): { token: string; path: string } {
  const path = resolveAdminTokenPath();
  let raw: string;
  try {
    raw = readFileSync(path, "utf8");
  } catch (error) {
    const errno = (error as NodeJS.ErrnoException)?.code;
    throw new OpenVaultAdminTokenUnavailableError(
      path,
      errno === "ENOENT" ? "file not found" : `cannot read file${errno ? `: ${errno}` : ""}`
    );
  }
  const token = raw.trim();
  if (!token) throw new OpenVaultAdminTokenUnavailableError(path, "file is empty");
  return { token, path };
}

export interface OpenVaultKey {
  id: string;
  label?: string;
  provider: string;
  role?: string;
  base_url?: string | null;
  masked_secret?: string;
  enabled: boolean;
  priority: number;
  precheck_status?: string;
  account_id?: string;
  lifecycle: string;
  custody?: string;
  [extra: string]: unknown;
}

async function readErrorType(response: Response): Promise<{ type?: string; detail?: string }> {
  try {
    const body = (await response.json()) as {
      error?: { type?: unknown };
      detail?: unknown;
    };
    return {
      type: typeof body?.error?.type === "string" ? body.error.type : undefined,
      detail: typeof body?.detail === "string" ? body.detail : undefined,
    };
  } catch {
    return {};
  }
}

async function fetchJson<T>(
  url: string,
  init: RequestInit & { headers?: Record<string, string> } = {},
  opts: { isSecretReveal?: boolean } = {}
): Promise<T> {
  const { token, path } = readAdminToken();
  const route = url.replace(openVaultBaseUrl(), "");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      ...init,
      headers: { ...(init.headers ?? {}), [ADMIN_HEADER]: token },
      signal: controller.signal,
    });
    if (response.status === 401) {
      throw new OpenVaultAdminTokenRejectedError(path, route);
    }
    if (response.status === 403 && opts.isSecretReveal) {
      const { type, detail } = await readErrorType(response);
      // openvault_forbidden is the guard's bad-bearer answer, not a sealed vault.
      // Other 403s on reveal (loopback-only, reveal intent) carry a detail that
      // does not mention the seal.
      if (type !== "openvault_forbidden" && (detail === undefined || /seal/i.test(detail))) {
        throw new OpenVaultKeyVaultSealedError(route);
      }
      throw new OpenVaultUnreachableError(
        `OpenVault responded 403 for ${route}${type ? ` (${type})` : detail ? `: ${detail}` : ""}.`
      );
    }
    if (!response.ok) {
      throw new OpenVaultUnreachableError(`OpenVault responded ${response.status} for ${route}.`);
    }
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof OpenVaultUnreachableError) throw error;
    throw new OpenVaultUnreachableError(
      `OpenVault KeyVault is unreachable at ${openVaultBaseUrl()}: ${
        error instanceof Error ? error.message : String(error)
      }`
    );
  } finally {
    clearTimeout(timeout);
  }
}

// ── /api/keys list cache (<=60s) ────────────────────────────────────────────

let keysCache: { at: number; keys: OpenVaultKey[] } | null = null;
let keysInflight: Promise<OpenVaultKey[]> | null = null;

async function fetchAllKeys(): Promise<OpenVaultKey[]> {
  if (keysCache && Date.now() - keysCache.at < CACHE_TTL_MS) return keysCache.keys;
  if (keysInflight) return keysInflight;

  keysInflight = fetchJson<{ keys: OpenVaultKey[] }>(`${openVaultBaseUrl()}/api/keys`)
    .then((body) => {
      const keys = Array.isArray(body?.keys) ? body.keys : [];
      keysCache = { at: Date.now(), keys };
      return keys;
    })
    .finally(() => {
      keysInflight = null;
    });
  return keysInflight;
}

// ── /api/keys/{id}/secret cache (<=60s per key id) ──────────────────────────

const secretCache = new Map<string, { at: number; secret: string }>();
const secretInflight = new Map<string, Promise<string>>();

async function revealSecret(keyId: string): Promise<string> {
  const cached = secretCache.get(keyId);
  if (cached && Date.now() - cached.at < CACHE_TTL_MS) return cached.secret;

  const pending = secretInflight.get(keyId);
  if (pending) return pending;

  const request = fetchJson<{ id: string; secret: string }>(
    `${openVaultBaseUrl()}/api/keys/${encodeURIComponent(keyId)}/secret`,
    { headers: { [REVEAL_HEADER]: REVEAL_HEADER_VALUE } },
    { isSecretReveal: true }
  )
    .then((body) => {
      secretCache.set(keyId, { at: Date.now(), secret: body.secret });
      return body.secret;
    })
    .finally(() => {
      secretInflight.delete(keyId);
    });
  secretInflight.set(keyId, request);
  return request;
}

// ── router provider id -> OpenVault `provider` value ────────────────────────

/**
 * Router registry ids (and a few common aliases/legacy spellings) that map
 * directly onto an OpenVault `provider` value. Anything not listed here falls
 * back to a base_url host match, then to "custom", see `mapToOpenVaultProvider`.
 */
const PROVIDER_ID_TO_OPENVAULT: Readonly<Record<string, string>> = {
  openai: "openai",
  anthropic: "anthropic",
  groq: "groq",
  openrouter: "openrouter",
  google: "google",
  gemini: "google",
  "google-ai-studio": "google",
  "vertex-ai": "google",
  mistral: "mistral",
  codestral: "mistral",
  deepseek: "deepseek",
  together: "together",
  togetherai: "together",
  cohere: "cohere",
  fireworks: "fireworks",
  cerebras: "cerebras",
  perplexity: "perplexity",
  xai: "xai",
  huggingface: "huggingface",
  nvidia: "nvidia",
  sambanova: "sambanova",
  novita: "novita",
  replicate: "replicate",
  ai21: "ai21",
};

const HOST_TO_OPENVAULT: ReadonlyArray<readonly [RegExp, string]> = [
  [/(^|\.)openai\.com$/, "openai"],
  [/(^|\.)anthropic\.com$/, "anthropic"],
  [/(^|\.)groq\.com$/, "groq"],
  [/(^|\.)openrouter\.ai$/, "openrouter"],
  [/(^|\.)googleapis\.com$/, "google"],
  [/generativelanguage\.googleapis\.com$/, "google"],
  [/(^|\.)mistral\.ai$/, "mistral"],
  [/(^|\.)deepseek\.com$/, "deepseek"],
  [/(^|\.)together\.(ai|xyz)$/, "together"],
  [/(^|\.)cohere\.(ai|com)$/, "cohere"],
  [/(^|\.)fireworks\.ai$/, "fireworks"],
  [/(^|\.)cerebras\.ai$/, "cerebras"],
  [/(^|\.)perplexity\.ai$/, "perplexity"],
  [/(^|\.)x\.ai$/, "xai"],
];

/** Map a FreeRoute registry provider id (+ optional base URL) to an OpenVault `provider` value. */
export function mapToOpenVaultProvider(routerProviderId: string, baseUrl?: string | null): string {
  const id = routerProviderId.trim().toLowerCase();
  const direct = PROVIDER_ID_TO_OPENVAULT[id];
  if (direct) return direct;

  if (baseUrl) {
    try {
      const host = new URL(baseUrl).hostname.toLowerCase();
      for (const [pattern, provider] of HOST_TO_OPENVAULT) {
        if (pattern.test(host)) return provider;
      }
    } catch {
      // Not a valid absolute URL, fall through to "custom".
    }
  }
  return "custom";
}

// ── Best-key selection ───────────────────────────────────────────────────────

function isUsable(key: OpenVaultKey): boolean {
  return key.enabled === true && key.lifecycle === "active";
}

/** Highest `priority` first (numerically greater = preferred, ties broken by list order). */
function pickBestKey(keys: OpenVaultKey[]): OpenVaultKey | null {
  let best: OpenVaultKey | null = null;
  for (const key of keys) {
    if (!isUsable(key)) continue;
    if (!best || (key.priority ?? 0) > (best.priority ?? 0)) best = key;
  }
  return best;
}

/** Lowercased origin + path without trailing slashes, or null when not an absolute http(s) URL. */
export function normalizeBaseUrl(value: string | null | undefined): string | null {
  if (!value || typeof value !== "string") return null;
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return `${url.protocol}//${url.host}${url.pathname.replace(/\/+$/, "")}`.toLowerCase();
  } catch {
    return null;
  }
}

// ── Public API ────────────────────────────────────────────────────────────

/**
 * Resolve the plaintext API key FreeRoute should use for `routerProviderId`
 * right now, or `null` when OpenVault has no usable (enabled + active) key
 * for it. Throws `OpenVaultUnreachableError` if OpenVault itself cannot be
 * reached, callers must propagate that as a loud 503, never a silent fallback
 * to a locally stored value.
 *
 * Selection, first match wins:
 *   1. a usable key whose `base_url` is the connection's base URL. This is how
 *      an OpenAI-compatible node (id `openai-compatible-*`, any host) finds
 *      the key the operator stored in OpenVault for that exact endpoint;
 *      the provider-id map below cannot name those.
 *   2. a usable key whose `provider` is the mapped OpenVault provider.
 */
export async function resolveProviderApiKey(
  routerProviderId: string,
  opts: { baseUrl?: string | null } = {}
): Promise<string | null> {
  const allKeys = await fetchAllKeys();

  const wantedBaseUrl = normalizeBaseUrl(opts.baseUrl);
  if (wantedBaseUrl) {
    const byEndpoint = pickBestKey(
      allKeys.filter((k) => normalizeBaseUrl(k.base_url ?? null) === wantedBaseUrl)
    );
    if (byEndpoint) return revealSecret(byEndpoint.id);
  }

  const ovProvider = mapToOpenVaultProvider(routerProviderId, opts.baseUrl);
  const best = pickBestKey(allKeys.filter((k) => k.provider === ovProvider));
  if (!best) return null;
  return revealSecret(best.id);
}

// ── Inbound client token verification (POST /api/apikeys/verify) ───────────
//
// Precedent: `isConfiguredEnvApiKey` in src/lib/db/apiKeys.ts accepts a token
// that has no row in the local `api_keys` table by checking it against
// OMNIROUTE_API_KEY, and `getApiKeyMetadata` synthesizes a metadata record
// for it. An OpenVault-issued token is the same shape of problem: no local
// row, so `verifyClientToken` here is the equivalent "is this token good"
// check, called from `validateApiKey`/`getApiKeyMetadata` in the same file.

export interface ClientTokenVerification {
  valid: boolean;
  keyId?: string;
  tier?: string;
}

const INVALID_VERIFICATION: ClientTokenVerification = { valid: false };

const verifyCache = new Map<string, { at: number; result: ClientTokenVerification }>();
const verifyInflight = new Map<string, Promise<ClientTokenVerification>>();

async function sha256Hex(value: string): Promise<string> {
  const { createHash } = await import("node:crypto");
  return createHash("sha256").update(value).digest("hex");
}

let unreachableWarningLoggedAt = 0;
function warnOnceVerifyUnreachable(error: unknown): void {
  const now = Date.now();
  // Rate-limit the console warning (once per 60s), a client hammering an
  // invalid token with OpenVault down must not spam the log.
  if (now - unreachableWarningLoggedAt < CACHE_TTL_MS) return;
  unreachableWarningLoggedAt = now;
  // The error message never contains the admin token, only its path.
  const code = isOpenVaultKeyVaultError(error) ? error.code : OPENVAULT_KEYVAULT_UNREACHABLE_CODE;
  console.warn(
    `[keyvault] ${code}: could not verify an inbound client token at ` +
      `${openVaultBaseUrl()}/api/apikeys/verify, treating it as invalid. ` +
      `${error instanceof Error ? error.message : String(error)}`
  );
}

/**
 * Verify an inbound client-facing bearer token against OpenVault
 * (`POST /api/apikeys/verify {"token"} -> {valid, key_id, tier}`), the
 * loopback-only endpoint OpenVault exposes for tokens it minted itself
 * (`POST /api/apikeys` on OpenVault's side, see http://127.0.0.1:3010/keys).
 *
 * Cached per sha256(token) for <=60s. Never resolves "valid" when OpenVault
 * is unreachable or answers with anything other than a clean `{valid:true}` -
 * an unreachable OpenVault must fail closed for inbound auth, the mirror image
 * of `resolveProviderApiKey` failing loud (503) for outbound provider keys:
 * here there is no safe "loud" option mid-request-auth, so the token is
 * simply rejected and a rate-limited warning naming the error code is logged.
 * The same applies to a missing or rejected admin token.
 */
export async function verifyClientToken(token: string | null | undefined): Promise<ClientTokenVerification> {
  if (!token || typeof token !== "string") return INVALID_VERIFICATION;

  const cacheKey = await sha256Hex(token);
  const cached = verifyCache.get(cacheKey);
  if (cached && Date.now() - cached.at < CACHE_TTL_MS) return cached.result;

  const pending = verifyInflight.get(cacheKey);
  if (pending) return pending;

  const request = fetchJson<{ valid: boolean; key_id?: string; tier?: string }>(
    `${openVaultBaseUrl()}/api/apikeys/verify`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token }),
    }
  )
    .then((body) => {
      const result: ClientTokenVerification =
        body && body.valid === true
          ? { valid: true, keyId: body.key_id, tier: body.tier }
          : INVALID_VERIFICATION;
      verifyCache.set(cacheKey, { at: Date.now(), result });
      return result;
    })
    .catch((error) => {
      warnOnceVerifyUnreachable(error);
      // Fail closed, never cache a transient-unreachable result as valid,
      // and don't poison the cache with "invalid" either (a real outage
      // should not lock out a legitimate token once OpenVault recovers).
      return INVALID_VERIFICATION;
    })
    .finally(() => {
      verifyInflight.delete(cacheKey);
    });
  verifyInflight.set(cacheKey, request);
  return request;
}

/** Test-only: drop the in-memory list/secret/verify caches. */
export function __resetKeyVaultCacheForTest(): void {
  unreachableWarningLoggedAt = 0;
  keysCache = null;
  keysInflight = null;
  secretCache.clear();
  secretInflight.clear();
  verifyCache.clear();
  verifyInflight.clear();
}
