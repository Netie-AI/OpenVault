// Copyright (c) 2026 Netie AI. MIT.
//
// FreeRoute provider-connection guards. Three jobs, one module:
//
// 1. assertConnectionWriteAllowed(): the DB-layer guard that every write to
//    provider_connections passes through (src/lib/db/providers.ts
//    createProviderConnection / updateProviderConnection). It refuses:
//      - a new connection for a provider classified by open-sse/netie/policy.ts
//        (consumer subscription pooling, browser session relay),
//      - credentials written onto an existing classified connection (an OAuth
//        refresh write-back, a captured browser login),
//      - a provider API key (apiKey column, providerSpecificData.extraApiKeys,
//        or any other secret-bearing providerSpecificData field) for any other
//        connection. Provider keys live only in OpenVault's KeyVault.
//    Routes call the same checks up front so the client gets the named 501
//    instead of whatever the route does with an unexpected throw.
//
// 2. resolveConnectionApiKey(): the one way a reader outside the request path
//    (model sync, model listing, translator, quota fetchers, health checks)
//    gets the key for a connection. Same lookup as materializeConnection in
//    src/sse/services/auth.ts. Never returns the DB column.
//
// 3. netieErrorResponse(): maps a NetieDisabledError, a
//    KeysManagedByOpenVaultError or a KeyVault error to its named JSON
//    response, or null for anything else.

import {
  classifyProvider,
  classifyProviderId,
  isNetieDisabledError,
  NetieDisabledError,
  renderDisabled,
  renderKeysManagedByOpenVault,
  type DisabledCode,
} from "@omniroute/open-sse/netie/policy.ts";
import { getRegistryEntry } from "@omniroute/open-sse/config/providerRegistry.ts";
import {
  isKeysManagedByOpenVaultError,
  isOpenVaultKeyVaultError,
  KeysManagedByOpenVaultError,
  openVaultErrorResponse,
  resolveProviderApiKey,
} from "./keyvault";

export { KeysManagedByOpenVaultError, isKeysManagedByOpenVaultError };

type JsonRecord = Record<string, unknown>;

/** Duck-typed NetieDisabledError check, so a copy of policy.ts in another bundle still matches. */
export function isNetieDisabled(error: unknown): error is NetieDisabledError {
  if (isNetieDisabledError(error)) return true;
  if (!error || typeof error !== "object") return false;
  const e = error as { status?: unknown; code?: unknown; name?: unknown };
  return e.status === 501 && e.name === "NetieDisabledError" && typeof e.code === "string";
}

/**
 * The named JSON response for a FreeRoute policy or KeyVault error, or null
 * when `error` is something else (the caller keeps its own handling).
 */
export function netieErrorResponse(error: unknown): Response | null {
  if (isNetieDisabled(error)) return renderDisabled(error.code as DisabledCode, error.message);
  if (isKeysManagedByOpenVaultError(error)) return renderKeysManagedByOpenVault(error.message);
  if (isOpenVaultKeyVaultError(error)) return openVaultErrorResponse(error);
  return null;
}

// ── Classification of a stored or incoming connection ─────────────────────

/** Disabled bucket for a connection row (by provider id and by its own authType), or null. */
export function classifyConnection(
  connection: { provider?: unknown; authType?: unknown } | null | undefined
): DisabledCode | null {
  if (!connection) return null;
  const provider = typeof connection.provider === "string" ? connection.provider : null;
  const authType = typeof connection.authType === "string" ? connection.authType : null;
  return (
    classifyProviderId(provider) ??
    classifyProvider({ id: provider, authType, authHeader: null, executor: null })
  );
}

// ── Secret detection ──────────────────────────────────────────────────────

// providerSpecificData field names that carry a credential. End-anchored, so
// metadata fields such as tokenExpiresAt, apiKeyHealth, accessKeyId,
// tokenEndpoint or codexAppServerTokenFile do not match. `pat` is a personal
// access token (qoder).
const SECRET_FIELD_PATTERN =
  /(^pat|api[-_]?keys?|secrets?|tokens?|cookies?|password|passphrase|authorization|session[-_]?key|account[-_]?key|runtime[-_]?key|access[-_]?key|private[-_]?key)$/i;

function hasValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.some(hasValue);
  if (typeof value === "object") return Object.values(value as JsonRecord).some(hasValue);
  return true;
}

function asRecord(value: unknown): JsonRecord {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as JsonRecord;
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as JsonRecord;
    } catch {
      return {};
    }
  }
  return {};
}

/** Names of the providerSpecificData fields in `psd` that hold a non-empty secret. */
export function findSecretProviderSpecificFields(psd: unknown): string[] {
  const record = asRecord(psd);
  return Object.keys(record).filter((key) => SECRET_FIELD_PATTERN.test(key) && hasValue(record[key]));
}

/** Copy of `psd` with every secret-bearing field removed. */
export function stripSecretProviderSpecificData(psd: unknown): JsonRecord {
  const record = { ...asRecord(psd) };
  for (const key of Object.keys(record)) {
    if (SECRET_FIELD_PATTERN.test(key)) delete record[key];
  }
  return record;
}

const CREDENTIAL_COLUMNS = ["apiKey", "accessToken", "refreshToken", "idToken"] as const;

/**
 * Credential columns and psd secret fields `data` would newly write. `existing`
 * is the stored row with credential columns decrypted. A value carried over
 * unchanged from it (a caller that spreads the whole connection back into an
 * update) is not a new write.
 */
export function findIncomingSecrets(data: JsonRecord, existing?: JsonRecord | null): string[] {
  const found: string[] = CREDENTIAL_COLUMNS.filter(
    (field) => hasValue(data[field]) && data[field] !== existing?.[field]
  );
  if ("providerSpecificData" in data) {
    const incoming = asRecord(data.providerSpecificData);
    const before = asRecord(existing?.providerSpecificData);
    for (const key of findSecretProviderSpecificFields(incoming)) {
      // A value carried over unchanged from the stored row is not a new write;
      // updateProviderConnection scrubs it instead of refusing the whole edit.
      if (JSON.stringify(incoming[key]) === JSON.stringify(before[key])) continue;
      found.push(`providerSpecificData.${key}`);
    }
  }
  return found;
}

/**
 * The DB-layer guard. `existing` is the stored row (camelCase, credential
 * columns decrypted) on an update, undefined on a create. Throws NetieDisabledError (501) or
 * KeysManagedByOpenVaultError (501); returns normally when the write is allowed.
 */
export function assertConnectionWriteAllowed(data: JsonRecord, existing?: JsonRecord | null): void {
  const provider = data.provider ?? existing?.provider;
  const authType = data.authType ?? existing?.authType;
  const classified = classifyConnection({ provider, authType });
  const secrets = findIncomingSecrets(data, existing);

  if (classified) {
    // Creating a classified connection is the pooling or relay feature itself.
    // On an existing row (left over from a restored backup), status writes stay
    // allowed so an operator can still disable or delete it; credentials do not.
    if (!existing || secrets.length > 0) throw new NetieDisabledError(classified);
    return;
  }

  if (secrets.length > 0) throw new KeysManagedByOpenVaultError(secrets);
}

/**
 * Route-level check for a create body that may hold one provider (`provider`)
 * or many entries (`entries[]`, each with its own optional `provider`). Returns
 * the named 501 to send, or null when the body carries no provider secret and
 * names no classified provider. Used by POST /api/providers/bulk and
 * /api/providers/import before schema validation, so a body that would only
 * fail validation for lack of a key still gets the named answer.
 */
export function rejectProviderSecretsInBody(body: unknown): Response | null {
  const root = asRecord(body);
  const entries = Array.isArray(root.entries) ? root.entries.map(asRecord) : [];
  const providers = [root.provider, ...entries.map((e) => e.provider)].filter(
    (p): p is string => typeof p === "string" && p.trim().length > 0
  );
  for (const provider of providers) {
    const code = classifyProviderId(provider);
    if (code) return renderDisabled(code);
  }
  const carriesSecret =
    hasValue(root.apiKey) ||
    findSecretProviderSpecificFields(root.providerSpecificData).length > 0 ||
    entries.some(
      (e) =>
        CREDENTIAL_COLUMNS.some((field) => hasValue(e[field])) ||
        findSecretProviderSpecificFields(e.providerSpecificData).length > 0
    );
  return carriesSecret ? renderKeysManagedByOpenVault() : null;
}

// ── KeyVault-backed key for a stored connection ───────────────────────────

/**
 * The provider key a reader should use for `connection`, from OpenVault, or
 * null when OpenVault has none. Throws NetieDisabledError for a classified
 * connection, and the KeyVault 503 errors from resolveProviderApiKey. Never
 * returns `connection.apiKey`.
 */
export async function resolveConnectionApiKey(
  connection: { provider?: unknown; authType?: unknown; providerSpecificData?: unknown } | null | undefined
): Promise<string | null> {
  if (!connection || typeof connection.provider !== "string") return null;
  const classified = classifyConnection(connection);
  if (classified) throw new NetieDisabledError(classified);
  const psd = asRecord(connection.providerSpecificData);
  const baseUrl =
    (typeof psd.baseUrl === "string" && psd.baseUrl) ||
    getRegistryEntry(connection.provider)?.baseUrl ||
    null;
  return resolveProviderApiKey(connection.provider, { baseUrl });
}

/**
 * Copy of `connection` with `apiKey` replaced by the KeyVault key and every
 * secret providerSpecificData field removed. For readers that hand a whole
 * connection object to a fetcher written against the upstream shape.
 */
export async function withKeyVaultApiKey<T extends JsonRecord>(connection: T): Promise<T> {
  const apiKey = await resolveConnectionApiKey(connection);
  return {
    ...connection,
    apiKey,
    providerSpecificData: stripSecretProviderSpecificData(connection.providerSpecificData),
  };
}

/**
 * Upstream credentials for a stored connection, for a route that sends its own
 * request (translator send/translate and similar): the API key from OpenVault,
 * no OAuth or session tokens, no stored providerSpecificData secrets. Throws
 * NetieDisabledError for a classified connection and the KeyVault 503 errors.
 */
export async function buildKeyVaultCredentials(connection: JsonRecord): Promise<{
  apiKey: string | null;
  accessToken: null;
  refreshToken: null;
  copilotToken: null;
  projectId: unknown;
  providerSpecificData: JsonRecord;
}> {
  const classified = classifyConnection(connection);
  if (classified) throw new NetieDisabledError(classified);
  const apiKey = connection.authType === "apikey" ? await resolveConnectionApiKey(connection) : null;
  return {
    apiKey,
    accessToken: null,
    refreshToken: null,
    copilotToken: null,
    projectId: connection.projectId,
    providerSpecificData: stripSecretProviderSpecificData(connection.providerSpecificData),
  };
}
