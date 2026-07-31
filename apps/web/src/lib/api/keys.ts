/**
 * The vault: keys, provider catalog, and environment harvesting.
 *
 * Design bias, stated up front because it drives what lives here: the user
 * should type as little as possible. A pasted key carries enough information
 * to identify its provider (see lib/vault/inferProvider), and the catalog
 * carries the rest — base URL, a sensible role, docs. So the UI derives every
 * field of `CreateKeyInput` from one paste; this module stays a thin, honest
 * mapping of the backend routes rather than hiding that inference.
 */

import {
  apiDelete,
  apiFetch,
  apiGet,
  apiPatch,
  apiPost,
  LONG_TIMEOUT_MS,
} from "./client";

export type KeyRole = "primary" | "backup" | "cheap" | "free";
export const KEY_ROLES: readonly KeyRole[] = ["primary", "backup", "cheap", "free"];

/** Set by the vault's precheck loop, which really does call the provider. */
export type PrecheckStatus = "ok" | "auth_fail" | "rate_limit" | "error" | "unknown";

export interface KeyRow {
  id: string;
  label: string;
  provider: string;
  role: string;
  priority: number;
  enabled: boolean;
  status: string;
  precheck_status: PrecheckStatus;
  masked_secret?: string;
  base_url?: string | null;
  last_latency_ms?: number | null;
  last_checked_at?: string | null;
  account_id?: string | null;
}

export interface ProviderSpec {
  id: string;
  name: string;
  base_url: string;
  default_role: KeyRole;
  tier: string;
  register_url: string;
  docs_url: string;
  health_path: string;
  openai_compatible: boolean;
  free_notes: string;
  needed_by: string[];
  status_page: string;
  placeholder_secret: string;
}

/** Masked by construction — `scan_environment` never returns raw values. */
export interface EnvCandidate {
  env_key: string;
  provider: string;
  known: boolean;
  masked: string;
}

export async function listKeys(signal?: AbortSignal): Promise<KeyRow[]> {
  const data = await apiGet<{ keys?: KeyRow[] }>("/api/keys", { signal });
  return data.keys ?? [];
}

export interface CreateKeyInput {
  label: string;
  provider: string;
  secret: string;
  role: KeyRole;
  base_url?: string;
  priority?: number;
}

export function createKey(input: CreateKeyInput): Promise<KeyRow> {
  return apiPost<KeyRow>("/api/keys", input);
}

export function updateKey(id: string, patch: Partial<CreateKeyInput> & { enabled?: boolean }) {
  return apiPatch<KeyRow>(`/api/keys/${id}`, patch);
}

export function deleteKey(id: string): Promise<unknown> {
  return apiDelete(`/api/keys/${id}`);
}

export function revokeKey(id: string): Promise<KeyRow> {
  return apiPost<KeyRow>(`/api/keys/${id}/revoke`);
}

export function rotateKey(id: string, newSecret: string): Promise<KeyRow> {
  return apiPost<KeyRow>(`/api/keys/${id}/rotate`, { new_secret: newSecret });
}

/** Probes the provider for real, so it is slow and must never block a render. */
export function precheckKey(id: string): Promise<{ status: PrecheckStatus; detail?: string }> {
  return apiPost(`/api/keys/${id}/precheck`, undefined, { timeoutMs: LONG_TIMEOUT_MS });
}

export function precheckAll(): Promise<unknown> {
  return apiPost("/api/keys/precheck-all", undefined, { timeoutMs: LONG_TIMEOUT_MS });
}

/**
 * Fetch one decrypted secret.
 *
 * The backend requires loopback AND this header — a bare GET is refused with
 * 428. That is deliberate: without the header a page the user merely has open
 * could read every credential. Only call this from an explicit user gesture,
 * never on mount, and never store the result.
 */
export function revealSecret(id: string): Promise<{ id: string; secret: string }> {
  return apiFetch<{ id: string; secret: string }>(`/api/keys/${id}/secret`, {
    headers: { "X-OpenVault-Reveal": "intentional" },
  });
}

/* ── Provider catalog ─────────────────────────────────────────────────── */

export async function listProviders(signal?: AbortSignal): Promise<ProviderSpec[]> {
  const data = await apiGet<{ providers?: ProviderSpec[] }>("/api/providers/catalog", {
    signal,
  });
  return data.providers ?? [];
}

export async function listFreeProviders(signal?: AbortSignal): Promise<ProviderSpec[]> {
  const data = await apiGet<{ providers?: ProviderSpec[] }>("/api/providers/free", {
    signal,
  });
  return data.providers ?? [];
}

/** What the vault still lacks — drives the "you're missing X" prompt. */
export function coverage(signal?: AbortSignal): Promise<Record<string, unknown>> {
  return apiGet("/api/providers/coverage", { signal });
}

/* ── Environment harvesting ───────────────────────────────────────────── */

export async function scanEnv(signal?: AbortSignal): Promise<EnvCandidate[]> {
  const data = await apiGet<{ candidates?: EnvCandidate[] }>(
    "/api/vault/env-scan",
    { signal },
  );
  return data.candidates ?? [];
}

/**
 * Import environment secrets. Defaults to a dry run server-side, so the
 * caller must opt in to writing — pass `false` only from a click.
 */
export function ingestEnv(dryRun = true): Promise<{ imported?: number; count?: number }> {
  return apiPost("/api/vault/ingest-env", { dry_run: dryRun }, { timeoutMs: LONG_TIMEOUT_MS });
}

export function seedEssentials(): Promise<unknown> {
  return apiPost("/api/vault/seed-essentials", undefined, { timeoutMs: LONG_TIMEOUT_MS });
}

/** Coverage gaps vs free/local catalog — drives the "add a free fallback" banner. */
export interface CoverageReport {
  vault_providers?: string[];
  free_or_local_catalog?: ProviderSpec[];
  missing_by_consumer?: Record<string, Array<{ id: string; name: string; register_url: string; tier: string }>>;
}

export function fetchCoverage(signal?: AbortSignal): Promise<CoverageReport> {
  return apiGet<CoverageReport>("/api/providers/coverage", { signal });
}

/** OpenFree token budget — real remaining from the limiter, not an estimate. */
export interface OpenFreeBudget {
  tier?: string;
  remaining?: number;
  remaining_tokens?: number;
  tokens_per_min?: number;
  requests_per_min?: number;
  [key: string]: unknown;
}

export function fetchOpenFreeBudget(signal?: AbortSignal): Promise<OpenFreeBudget> {
  return apiGet<OpenFreeBudget>("/api/openfree/ratelimit", {
    signal,
    query: { identity: "local", tier: "free" },
  });
}
