/**
 * Passwords and payment cards — thin client for `/api/secrets*`.
 *
 * Mirrors `keys.ts`: list is masks-only; reveal requires an explicit gesture
 * and the `X-OpenVault-Reveal` header. Never invent routes; never send CVV
 * (backend refuses it with 400 — that refusal is the honesty check).
 */

import { apiDelete, apiFetch, apiGet, apiPatch, apiPost } from "./client";

export type SecretKind = "password" | "payment_card";
export type SecretLifecycle = "active" | "revoked" | "rotated" | "compromised";

/** Public row — never contains plaintext password or full PAN. */
export interface SecretRow {
  id: string;
  kind: SecretKind;
  label: string;
  masked: string;
  account_id: string | null;
  lifecycle: SecretLifecycle;
  replaced_by: string | null;
  created_at: number;
  updated_at: number;
  last_revealed_at: number | null;
  username: string;
  url: string;
  brand: string;
  last4: string;
  exp_month: number | null;
  exp_year: number | null;
  cardholder: string;
}

export interface CreatePasswordInput {
  label: string;
  password: string;
  username?: string;
  url?: string;
  account_id?: string | null;
}

export interface CreateCardInput {
  label: string;
  pan: string;
  exp_month: number;
  exp_year: number;
  cardholder?: string;
  account_id?: string | null;
}

export interface SecretMetaPatch {
  label?: string;
  username?: string;
  url?: string;
  cardholder?: string;
  exp_month?: number;
  exp_year?: number;
  account_id?: string | null;
}

export interface RotateSecretInput {
  new_password?: string;
  new_pan?: string;
  exp_month?: number;
  exp_year?: number;
  label_suffix?: string;
}

export interface VaultStatus {
  ok?: boolean;
  sealed: boolean;
  passphrase_configured: boolean;
  wrap_method: string | null;
}

export async function listSecrets(
  opts?: { kind?: SecretKind; account_id?: string; signal?: AbortSignal },
): Promise<SecretRow[]> {
  const data = await apiGet<{ secrets?: SecretRow[] }>("/api/secrets", {
    signal: opts?.signal,
    query: {
      kind: opts?.kind,
      account_id: opts?.account_id,
    },
  });
  return data.secrets ?? [];
}

export function createPassword(input: CreatePasswordInput): Promise<SecretRow> {
  return apiPost<SecretRow>("/api/secrets/passwords", input);
}

export function createCard(input: CreateCardInput): Promise<SecretRow> {
  return apiPost<SecretRow>("/api/secrets/cards", input);
}

export function updateSecret(id: string, patch: SecretMetaPatch): Promise<SecretRow> {
  return apiPatch<SecretRow>(`/api/secrets/${encodeURIComponent(id)}`, patch);
}

export function deleteSecret(id: string): Promise<{ deleted: boolean }> {
  return apiDelete<{ deleted: boolean }>(`/api/secrets/${encodeURIComponent(id)}`);
}

export function revokeSecret(
  id: string,
  reason = "operator_revoke",
): Promise<SecretRow> {
  return apiPost<SecretRow>(`/api/secrets/${encodeURIComponent(id)}/revoke`, { reason });
}

export function rotateSecret(id: string, input: RotateSecretInput): Promise<SecretRow> {
  return apiPost<SecretRow>(`/api/secrets/${encodeURIComponent(id)}/rotate`, input);
}

/**
 * Decrypt one password or full PAN. Call only from an explicit click.
 * Do not store the result longer than the UI needs to show it.
 */
export function revealSecretValue(
  id: string,
): Promise<{ id: string; kind: SecretKind; last4: string; brand: string; secret: string }> {
  return apiFetch(`/api/secrets/${encodeURIComponent(id)}/reveal`, {
    headers: { "X-OpenVault-Reveal": "intentional" },
  });
}

export function fetchVaultStatus(signal?: AbortSignal): Promise<VaultStatus> {
  return apiGet<VaultStatus>("/api/vault/status", { signal });
}

export function unsealVault(passphrase: string): Promise<VaultStatus> {
  return apiPost<VaultStatus>("/api/vault/unseal", { passphrase });
}

export function lockVault(): Promise<VaultStatus> {
  return apiPost<VaultStatus>("/api/vault/lock");
}
