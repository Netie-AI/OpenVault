/**
 * Secret handling for destination configs.
 *
 * Which keys are secret is declared by the destination type (`secretFields`), so this
 * module stays generic: encrypt on write, decrypt only when a client is constructed,
 * and redact before anything reaches an API response.
 */

import { decrypt, encrypt, isEncryptionEnabled } from "@/lib/db/encryption";
import { resolveProviderApiKey } from "@/lib/netie/keyvault";
import { getLogExportDestinationType } from "./registry";

/** Placeholder returned by the API in place of a stored secret. */
export const SECRET_PLACEHOLDER = "__stored__";

function secretKeysFor(type: string): readonly string[] {
  return getLogExportDestinationType(type)?.secretFields ?? [];
}

/**
 * True when this destination type stores a credential AND field encryption is off.
 *
 * `encrypt()` is a silent passthrough without STORAGE_ENCRYPTION_KEY, which is the
 * default for a fresh install — so writing a service-account key would land it in
 * SQLite as plaintext. Callers refuse the write instead (the same guard the Telegram
 * webhook uses).
 */
export function requiresEncryptionKey(type: string, config: Record<string, unknown>): boolean {
  if (isEncryptionEnabled()) return false;
  return secretKeysFor(type).some((key) => {
    const value = config[key];
    return typeof value === "string" && value.length > 0;
  });
}

/** Encrypt every declared secret key. Non-secret keys pass through untouched. */
export function encryptDestinationConfig(
  type: string,
  config: Record<string, unknown>
): Record<string, unknown> {
  const secretKeys = secretKeysFor(type);
  if (secretKeys.length === 0) return { ...config };
  const out: Record<string, unknown> = { ...config };
  for (const key of secretKeys) {
    const value = out[key];
    if (typeof value === "string" && value.length > 0) out[key] = encrypt(value);
  }
  return out;
}

/** Decrypt declared secret keys for runtime use. Never feed the result to a response. */
export function decryptDestinationConfig(
  type: string,
  config: Record<string, unknown>
): Record<string, unknown> {
  const secretKeys = secretKeysFor(type);
  if (secretKeys.length === 0) return { ...config };
  const out: Record<string, unknown> = { ...config };
  for (const key of secretKeys) {
    const value = out[key];
    if (typeof value === "string" && value.length > 0) out[key] = decrypt(value) ?? "";
  }
  return out;
}

/**
 * Replace declared secrets with a placeholder for API responses. A stored secret
 * becomes SECRET_PLACEHOLDER; an absent one stays absent, so the UI can tell
 * "configured" from "never set".
 */
export function redactDestinationConfig(
  type: string,
  config: Record<string, unknown>
): Record<string, unknown> {
  const secretKeys = secretKeysFor(type);
  const out: Record<string, unknown> = { ...config };
  for (const key of secretKeys) {
    const value = out[key];
    if (typeof value === "string" && value.length > 0) out[key] = SECRET_PLACEHOLDER;
    else delete out[key];
  }
  return out;
}

/**
 * Merge an incoming config over the stored one, keeping the stored ciphertext wherever
 * the caller sent back the placeholder (an edit that did not retype the secret).
 */
export function mergeDestinationConfig(
  type: string,
  storedConfig: Record<string, unknown>,
  incomingConfig: Record<string, unknown>
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...incomingConfig };
  for (const key of secretKeysFor(type)) {
    if (merged[key] === SECRET_PLACEHOLDER || merged[key] === undefined) {
      if (storedConfig[key] !== undefined) merged[key] = storedConfig[key];
      else delete merged[key];
    }
  }
  return merged;
}

/**
 * Secret fields in `config` that carry a value to store: non-empty and not the
 * placeholder a form sends back for an untouched secret. FreeRoute does not store
 * these; the routes answer 501 keys_managed_by_openvault for them.
 */
export function findIncomingSecretFields(type: string, config: Record<string, unknown>): string[] {
  return secretKeysFor(type).filter((key) => {
    const value = config[key];
    return typeof value === "string" && value.trim().length > 0 && value !== SECRET_PLACEHOLDER;
  });
}

/**
 * Config as the type's schema sees it when the credential is not stored here: every
 * secret field is filled with the placeholder, so a required field still validates,
 * and the stored copy drops it (`withoutSecretFields`).
 */
export function withSecretPlaceholders(
  type: string,
  config: Record<string, unknown>
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...config };
  for (const key of secretKeysFor(type)) out[key] = SECRET_PLACEHOLDER;
  return out;
}

/** Config without its secret fields, which is all FreeRoute stores for a destination. */
export function withoutSecretFields(
  type: string,
  config: Record<string, unknown>
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...config };
  for (const key of secretKeysFor(type)) delete out[key];
  return out;
}

/** Message for the 501 a destination write answers when it carries a credential. */
export function logExportCredentialMessage(type: string, fields: readonly string[]): string {
  return (
    `${fields.join(", ")} would be stored in FreeRoute's own database, and FreeRoute does not ` +
    `keep credentials. Saving a ${type} credential here is disabled. Store it in OpenVault ` +
    `with provider ${type} (http://127.0.0.1:3010/keys); the export reads it from there.`
  );
}

/**
 * Runtime config for a destination: the stored non-secret fields, with each secret
 * field read from OpenVault (provider = the destination type id). A copy an older
 * build left in the database is ignored. Throws the KeyVault 503 errors, or an
 * Error naming the missing credential.
 */
export async function resolveVaultDestinationConfig(
  type: string,
  config: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const out: Record<string, unknown> = { ...config };
  for (const key of secretKeysFor(type)) {
    const secret = await resolveProviderApiKey(type);
    if (!secret) {
      throw new Error(
        `No ${type} credential in OpenVault. Add one at http://127.0.0.1:3010/keys with provider ${type}.`
      );
    }
    out[key] = secret;
  }
  return out;
}
