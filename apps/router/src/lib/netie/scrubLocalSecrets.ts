// Copyright (c) 2026 Netie AI. MIT.
//
// Removes provider secrets that reached FreeRoute's SQLite store without going
// through createProviderConnection/updateProviderConnection: a SQLite backup
// uploaded through /api/db-backups/import, a restored backup, a JSON settings
// import (/api/settings/import-json), the legacy db.json migration, or a
// database written by an older build.
//
// For every provider_connections row:
//   - a classified provider (consumer subscription, browser session): all
//     credential columns are cleared, secret providerSpecificData fields are
//     removed, and the row is deactivated. The row itself stays so the
//     operator can see and delete it.
//   - any other provider: api_key is cleared and secret providerSpecificData
//     fields (extraApiKeys, consoleApiKey, cookies, ...) are removed. The key
//     for the connection comes from OpenVault at request time.
//
// With dropClientKeys, rows in api_keys (FreeRoute's own inbound client keys)
// are deleted too. OpenVault issues those; an import must not bring back local
// ones. Boot does not pass it.
//
// Called from src/lib/db/core.ts each time the database is opened, and from
// the import routes right after they write.

import {
  classifyConnection,
  findSecretProviderSpecificFields,
  stripSecretProviderSpecificData,
} from "./providerGuards";

interface StatementLike {
  all: (...params: unknown[]) => unknown[];
  run: (...params: unknown[]) => unknown;
}

interface DbLike {
  prepare: (sql: string) => StatementLike;
}

interface ConnectionSecretRow {
  id: string;
  provider: string | null;
  auth_type: string | null;
  api_key: string | null;
  access_token: string | null;
  refresh_token: string | null;
  id_token: string | null;
  is_active: number | null;
  provider_specific_data: string | null;
}

export interface ScrubResult {
  clearedApiKeys: number;
  strippedProviderData: number;
  disabledClassified: number;
  droppedClientKeys: number;
}

function parsePsd(raw: string | null): Record<string, unknown> {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

export function scrubLocalProviderSecrets(
  db: DbLike,
  opts: { dropClientKeys?: boolean } = {}
): ScrubResult {
  const result: ScrubResult = {
    clearedApiKeys: 0,
    strippedProviderData: 0,
    disabledClassified: 0,
    droppedClientKeys: 0,
  };

  const rows = db
    .prepare(
      "SELECT id, provider, auth_type, api_key, access_token, refresh_token, id_token, " +
        "is_active, provider_specific_data FROM provider_connections"
    )
    .all() as ConnectionSecretRow[];

  const updateClassified = db.prepare(
    "UPDATE provider_connections SET api_key = NULL, access_token = NULL, refresh_token = NULL, " +
      "id_token = NULL, is_active = 0, provider_specific_data = ? WHERE id = ?"
  );
  const updateOther = db.prepare(
    "UPDATE provider_connections SET api_key = NULL, provider_specific_data = ? WHERE id = ?"
  );

  for (const row of rows) {
    const psd = parsePsd(row.provider_specific_data);
    const psdSecrets = findSecretProviderSpecificFields(psd);
    const strippedPsd = psdSecrets.length > 0 ? JSON.stringify(stripSecretProviderSpecificData(psd)) : row.provider_specific_data;

    if (classifyConnection({ provider: row.provider, authType: row.auth_type })) {
      const holdsSomething =
        row.api_key || row.access_token || row.refresh_token || row.id_token || row.is_active || psdSecrets.length > 0;
      if (!holdsSomething) continue;
      updateClassified.run(strippedPsd, row.id);
      result.disabledClassified += 1;
      continue;
    }

    if (!row.api_key && psdSecrets.length === 0) continue;
    updateOther.run(strippedPsd, row.id);
    if (row.api_key) result.clearedApiKeys += 1;
    if (psdSecrets.length > 0) result.strippedProviderData += 1;
  }

  if (opts.dropClientKeys) {
    const counted = db.prepare("SELECT COUNT(*) AS n FROM api_keys").all() as Array<{ n: number }>;
    db.prepare("DELETE FROM api_keys").run();
    result.droppedClientKeys = Number(counted[0]?.n ?? 0);
  }

  const touched =
    result.clearedApiKeys + result.strippedProviderData + result.disabledClassified + result.droppedClientKeys;
  if (touched > 0) {
    console.warn(
      `[FreeRoute] Removed secrets stored outside OpenVault: ${result.clearedApiKeys} api_key ` +
        `column(s), ${result.strippedProviderData} providerSpecificData secret set(s), ` +
        `${result.disabledClassified} disabled subscription/session connection(s), ` +
        `${result.droppedClientKeys} local client key(s). Provider keys belong in OpenVault ` +
        `(http://127.0.0.1:3010/keys).`
    );
  }
  return result;
}

/**
 * Same rule applied to a JSON import before it is written, so the secrets
 * never touch the database file: classified connections lose their
 * credentials and are imported inactive, every other connection loses apiKey
 * and its secret providerSpecificData fields, and local client keys are
 * dropped. Used by src/lib/db/jsonMigration.ts runJsonMigration.
 */
export function sanitizeImportedConnections<
  T extends { providerConnections?: Record<string, unknown>[]; apiKeys?: Record<string, unknown>[] },
>(data: T): T {
  const providerConnections = (data.providerConnections ?? []).map((conn) => {
    const authType = typeof conn.authType === "string" ? conn.authType : "oauth";
    const providerSpecificData = conn.providerSpecificData
      ? stripSecretProviderSpecificData(conn.providerSpecificData)
      : conn.providerSpecificData;
    if (classifyConnection({ provider: conn.provider, authType })) {
      return {
        ...conn,
        apiKey: null,
        accessToken: null,
        refreshToken: null,
        idToken: null,
        isActive: false,
        providerSpecificData,
      };
    }
    return { ...conn, apiKey: null, providerSpecificData };
  });
  return { ...data, providerConnections, apiKeys: [] };
}
