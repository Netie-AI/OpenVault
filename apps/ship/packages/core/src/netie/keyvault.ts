// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// Modified by Netie AI, 2026: send the X-OpenVault-Admin token, map 401 and
// 403 openvault_forbidden to named 503 errors, skip non-active keys. Node
// built-ins are loaded with process.getBuiltinModule, not static imports,
// because @repo/core is also bundled into the dashboard's client code.
/**
 * The ONLY client FreeBuild uses to read third-party provider API keys.
 *
 * FreeBuild (this fork) never stores a provider API key in its own database,
 * files, or environment. Every provider credential that fits OpenVault's
 * KeyVault model — one secret, addressed by a stable label/provider id — is
 * read from OpenVault at use time, cached in memory only, and never written
 * to disk or logs.
 *
 * Server-side only: this module reads `process.env.OPENVAULT_URL` and talks
 * to OpenVault's loopback API. It must never be imported by dashboard
 * client-side code (nothing here should reach the browser bundle).
 *
 * Contract (OpenVault, loopback-only):
 *   GET  /api/keys                      -> { keys: OpenVaultKey[] }            (no plaintext)
 *   GET  /api/keys/{id}/secret          -> { id, secret }  — header
 *        X-OpenVault-Reveal: intentional required; audited; 403 if the vault
 *        is sealed, 404 if the id is unknown.
 *   Both need header X-OpenVault-Admin: <token>. The token is the content of a
 *   file at $OPENVAULT_ADMIN_TOKEN_PATH, else $OPENVAULT_HOME/admin_token, else
 *   ~/.openvault/admin_token. A missing or wrong token gets 401
 *   openvault_unauthenticated. A 403 with error.type "openvault_forbidden" is
 *   the guard refusing the caller, not a sealed vault.
 *
 * See BRIEF.md section 4 for the full contract this implements.
 */

import { AppError } from "../errors";

/** A key row as OpenVault's `GET /api/keys` returns it. Never carries plaintext. */
export interface OpenVaultKey {
  id: string;
  label: string;
  /** Lowercase id, e.g. "openai", "anthropic", "custom". FreeBuild's own
   *  credentials (Cloudflare, a registry login, ...) are not LLM providers,
   *  so they are expected to arrive as provider "custom" with a `label`
   *  FreeBuild matches against — see {@link findKeyForFreeBuildProvider}. */
  provider: string;
  role?: string;
  base_url?: string | null;
  masked_secret?: string;
  enabled: boolean;
  priority?: number;
  precheck_status?: string;
  account_id?: string;
  lifecycle?: string;
  custody?: string;
  [extra: string]: unknown;
}

/**
 * FreeBuild's own credential catalog (packages/core/src/credentials.ts)
 * entries that are a clean fit for OpenVault's one-secret-per-key model:
 * a single provider-wide API token, no per-resource pairing. Kept here
 * (not in credentials.ts) so the "does this provider's secret live in
 * OpenVault" question has exactly one answer, read by both the write path
 * (credential.service.ts refuses to store it) and the read path (resolves
 * it from OpenVault instead of the local DB).
 *
 * `docker-registry` is deliberately NOT here: it is a per-host (selector)
 * USERNAME + secret pair, and OpenVault's key model has no per-selector
 * scoping contract defined for that shape yet. Forcing it through a
 * label-match convention now would be guesswork — see the seam noted in
 * the fork report (file:line packages/core/src/credentials.ts, the
 * "docker-registry" entry) rather than silently mishandling it here.
 */
export const OPENVAULT_MANAGED_CREDENTIAL_PROVIDERS: ReadonlySet<string> = new Set([
  "cloudflare",
]);

/** OpenVault could not be reached at all (connection refused, DNS, timeout). */
export class OpenVaultKeyvaultUnreachableError extends AppError {
  constructor(cause?: unknown) {
    super(
      "Could not reach OpenVault's KeyVault. Provider keys are managed there; " +
        "start OpenVault or check OPENVAULT_URL.",
      503,
      "openvault_keyvault_unreachable",
    );
    this.name = "OpenVaultKeyvaultUnreachableError";
    if (cause instanceof Error) this.stack += `\nCaused by: ${cause.stack ?? cause.message}`;
  }
}

/** The vault exists and answered, but is sealed (locked) right now. */
export class OpenVaultSealedError extends AppError {
  constructor() {
    super("OpenVault's KeyVault is sealed. Unlock it to use provider keys.", 503, "openvault_keyvault_sealed");
    this.name = "OpenVaultSealedError";
  }
}

/** The admin token file is missing, unreadable or empty. Carries the path, never the token. */
export class OpenVaultAdminTokenUnavailableError extends AppError {
  constructor(tokenPath: string) {
    super(
      `OpenVault admin token not found at ${tokenPath}. Start OpenVault once to create it, ` +
        "or set OPENVAULT_ADMIN_TOKEN_PATH.",
      503,
      "openvault_admin_token_unavailable",
    );
    this.name = "OpenVaultAdminTokenUnavailableError";
  }
}

/** OpenVault answered 401: the admin token was missing or wrong. */
export class OpenVaultAdminTokenRejectedError extends AppError {
  constructor(tokenPath: string) {
    super(
      `OpenVault rejected the admin token read from ${tokenPath}. ` +
        "Check that it matches the token OpenVault created.",
      503,
      "openvault_admin_token_rejected",
    );
    this.name = "OpenVaultAdminTokenRejectedError";
  }
}

/** OpenVault's guard refused this caller (403 with error.type "openvault_forbidden"). */
export class OpenVaultForbiddenError extends AppError {
  constructor() {
    super(
      "OpenVault refused this request (openvault_forbidden). FreeBuild must reach it over loopback.",
      503,
      "openvault_forbidden",
    );
    this.name = "OpenVaultForbiddenError";
  }
}

function baseUrl(): string {
  return (process.env.OPENVAULT_URL?.trim() || "http://127.0.0.1:5000").replace(/\/+$/, "");
}

/**
 * Node built-ins, loaded at call time. A static `import "node:fs"` here breaks
 * the dashboard build: @repo/core is transpiled into client bundles, and
 * webpack cannot resolve the node: scheme there. Needs Node 22.3 or later.
 */
function nodeBuiltins() {
  const load = typeof process !== "undefined" ? process.getBuiltinModule : undefined;
  if (!load) throw new Error("The OpenVault client runs only on Node 22.3 or later.");
  return {
    fs: load("node:fs/promises"),
    os: load("node:os"),
    path: load("node:path"),
  };
}

/** Where OpenVault keeps its admin token. Same precedence as OpenVault itself. */
export function adminTokenPath(): string {
  const explicit = process.env.OPENVAULT_ADMIN_TOKEN_PATH?.trim();
  if (explicit) return explicit;
  const { os, path } = nodeBuiltins();
  const home = process.env.OPENVAULT_HOME?.trim();
  if (home) return path.join(home, "admin_token");
  return path.join(os.homedir(), ".openvault", "admin_token");
}

/** Read fresh on every request so a rotated token is picked up without a restart. */
async function readAdminToken(tokenPath: string): Promise<string> {
  const { fs } = nodeBuiltins();
  let token: string;
  try {
    token = (await fs.readFile(tokenPath, "utf8")).trim();
  } catch {
    throw new OpenVaultAdminTokenUnavailableError(tokenPath);
  }
  if (!token) throw new OpenVaultAdminTokenUnavailableError(tokenPath);
  return token;
}

async function isForbiddenBody(res: Response): Promise<boolean> {
  try {
    const body = (await res.json()) as { error?: { type?: unknown } } | null;
    return body?.error?.type === "openvault_forbidden";
  } catch {
    return false;
  }
}

/**
 * One authenticated request to OpenVault. Handles the cases every endpoint
 * shares (network failure, 401, 403 openvault_forbidden) and hands any other
 * response back. `forbiddenChecked` tells the caller a 403 body was already
 * read and was not openvault_forbidden.
 */
async function openVaultRequest(
  path: string,
  extraHeaders: Record<string, string> = {},
): Promise<{ res: Response; forbiddenChecked: boolean }> {
  const tokenPath = adminTokenPath();
  const token = await readAdminToken(tokenPath);
  let res: Response;
  try {
    res = await fetch(`${baseUrl()}${path}`, {
      headers: { ...extraHeaders, "X-OpenVault-Admin": token },
    });
  } catch (err) {
    // Network-level failure (refused, DNS, timeout). Never fall back to a
    // local store silently; the caller must fail loud.
    throw new OpenVaultKeyvaultUnreachableError(err);
  }
  if (res.status === 401) throw new OpenVaultAdminTokenRejectedError(tokenPath);
  if (res.status === 403) {
    if (await isForbiddenBody(res)) throw new OpenVaultForbiddenError();
    return { res, forbiddenChecked: true };
  }
  return { res, forbiddenChecked: false };
}

/** Every key OpenVault currently holds (no plaintext). */
export async function listKeys(): Promise<OpenVaultKey[]> {
  const { res } = await openVaultRequest("/api/keys");
  if (!res.ok) {
    throw new OpenVaultKeyvaultUnreachableError(
      new Error(`OpenVault responded ${res.status} for /api/keys`),
    );
  }
  const data = (await res.json()) as { keys?: OpenVaultKey[] };
  return data.keys ?? [];
}

/** A key FreeBuild may use: enabled, and active when OpenVault reports a lifecycle. */
function isUsable(k: OpenVaultKey): boolean {
  if (!k.enabled) return false;
  return k.lifecycle == null || k.lifecycle === "active";
}

/**
 * The FreeBuild-managed key for `providerId` (see
 * {@link OPENVAULT_MANAGED_CREDENTIAL_PROVIDERS}), or undefined when the
 * operator has not added one yet. Matches by OpenVault's own `provider` id
 * first (an operator who typed "cloudflare" as the provider), then falls
 * back to a case-insensitive label match (a "custom" entry labeled
 * "Cloudflare" / "Cloudflare API token") — OpenVault's fixed provider enum
 * has no "cloudflare" member, so `custom` + label is the expected shape.
 * Disabled keys, and keys whose lifecycle is present and not "active"
 * (revoked, replaced), are skipped; the first usable match wins.
 */
export async function findKeyForFreeBuildProvider(
  providerId: string,
): Promise<OpenVaultKey | undefined> {
  return (await findKeysForFreeBuildProvider(providerId))[0];
}

/**
 * Every usable key (see isUsable) matching `providerId`. An operator may label more than
 * one OpenVault key for the same FreeBuild provider (e.g. one Cloudflare
 * token per zone they own), same as multiple credentials of one provider
 * used to live in the local `credential` table.
 */
export async function findKeysForFreeBuildProvider(
  providerId: string,
): Promise<OpenVaultKey[]> {
  const keys = await listKeys();
  const wanted = providerId.toLowerCase();
  const usable = keys.filter(isUsable);
  const byProvider = usable.filter((k) => k.provider?.toLowerCase() === wanted);
  if (byProvider.length) return byProvider;
  return usable.filter((k) => k.label?.toLowerCase().includes(wanted));
}

// Plaintext cache: id -> { secret, expiresAt }. In memory ONLY, short TTL, and
// this module intentionally has no code path that writes a secret to disk,
// a log line, or a response body other than the one the caller asked for.
const SECRET_TTL_MS = 60_000;
const secretCache = new Map<string, { secret: string; expiresAt: number }>();

/** The live plaintext secret for a key id. Cached in memory for <=60s. */
export async function getSecret(id: string): Promise<string> {
  const cached = secretCache.get(id);
  if (cached && cached.expiresAt > Date.now()) return cached.secret;

  const { res, forbiddenChecked } = await openVaultRequest(
    `/api/keys/${encodeURIComponent(id)}/secret`,
    { "X-OpenVault-Reveal": "intentional" },
  );
  // A 403 that is not openvault_forbidden is the reveal refusing a sealed vault.
  if (forbiddenChecked) throw new OpenVaultSealedError();
  if (res.status === 404) {
    throw new AppError(`OpenVault has no key "${id}".`, 404, "openvault_key_not_found");
  }
  if (!res.ok) {
    throw new OpenVaultKeyvaultUnreachableError(
      new Error(`OpenVault responded ${res.status} for /api/keys/${id}/secret`),
    );
  }
  const data = (await res.json()) as { id: string; secret: string };
  secretCache.set(id, { secret: data.secret, expiresAt: Date.now() + SECRET_TTL_MS });
  return data.secret;
}

/** Test-only: drop the in-memory secret cache between cases. */
export function _resetSecretCacheForTests(): void {
  secretCache.clear();
}
