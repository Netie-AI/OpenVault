// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
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

function baseUrl(): string {
  return (process.env.OPENVAULT_URL?.trim() || "http://127.0.0.1:5000").replace(/\/+$/, "");
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${baseUrl()}${path}`, init);
  } catch (err) {
    // Network-level failure (refused, DNS, timeout) — never fall back to a
    // local store silently; the caller must fail loud.
    throw new OpenVaultKeyvaultUnreachableError(err);
  }
  if (res.status === 403) throw new OpenVaultSealedError();
  if (!res.ok) {
    throw new OpenVaultKeyvaultUnreachableError(
      new Error(`OpenVault responded ${res.status} for ${path}`),
    );
  }
  return (await res.json()) as T;
}

/** Every key OpenVault currently holds (no plaintext). */
export async function listKeys(): Promise<OpenVaultKey[]> {
  const data = await fetchJson<{ keys: OpenVaultKey[] }>("/api/keys");
  return data.keys ?? [];
}

/**
 * The FreeBuild-managed key for `providerId` (see
 * {@link OPENVAULT_MANAGED_CREDENTIAL_PROVIDERS}), or undefined when the
 * operator has not added one yet. Matches by OpenVault's own `provider` id
 * first (an operator who typed "cloudflare" as the provider), then falls
 * back to a case-insensitive label match (a "custom" entry labeled
 * "Cloudflare" / "Cloudflare API token") — OpenVault's fixed provider enum
 * has no "cloudflare" member, so `custom` + label is the expected shape.
 * Disabled keys are skipped; the first enabled match wins.
 */
export async function findKeyForFreeBuildProvider(
  providerId: string,
): Promise<OpenVaultKey | undefined> {
  return (await findKeysForFreeBuildProvider(providerId))[0];
}

/**
 * Every enabled key matching `providerId` — an operator may label more than
 * one OpenVault key for the same FreeBuild provider (e.g. one Cloudflare
 * token per zone they own), same as multiple credentials of one provider
 * used to live in the local `credential` table.
 */
export async function findKeysForFreeBuildProvider(
  providerId: string,
): Promise<OpenVaultKey[]> {
  const keys = await listKeys();
  const wanted = providerId.toLowerCase();
  const byProvider = keys.filter((k) => k.enabled && k.provider?.toLowerCase() === wanted);
  if (byProvider.length) return byProvider;
  return keys.filter((k) => k.enabled && k.label?.toLowerCase().includes(wanted));
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

  let res: Response;
  try {
    res = await fetch(`${baseUrl()}/api/keys/${encodeURIComponent(id)}/secret`, {
      headers: { "X-OpenVault-Reveal": "intentional" },
    });
  } catch (err) {
    throw new OpenVaultKeyvaultUnreachableError(err);
  }
  if (res.status === 403) throw new OpenVaultSealedError();
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
