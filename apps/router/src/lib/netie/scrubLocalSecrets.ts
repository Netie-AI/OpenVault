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
// Two other places held a provider key and are cleared the same way: the cloud
// agent table (cloud_agent_credentials, Devin/Jules/Codex-cloud/Cursor-cloud API
// keys) and the semantic-cache embedding API key kept in key_value.
//
// Clearing a column or deleting a row does not remove the bytes from the SQLite
// file: the old cell stays in the page until something overwrites it. So the
// scrub runs with PRAGMA secure_delete = ON, which zeroes every cell it frees,
// and then VACUUMs, which rewrites the whole file without the freed pages (and
// checkpoints the WAL so no frame still carries the old page). A test greps the
// raw file bytes for the secret afterwards (netie/keyvault.test.mjs).
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
  droppedAgentCredentials: number;
  clearedEmbeddingKeys: number;
  /** True when the scrub changed something and the file was VACUUMed after it. */
  vacuumed: boolean;
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


/** Run a statement that may or may not return rows (PRAGMA, VACUUM) on any of the SQLite adapters. */
function execStatement(db: DbLike, sql: string): unknown[] {
  const statement = db.prepare(sql);
  try {
    return statement.all();
  } catch {
    // better-sqlite3 refuses .all() on a statement that returns no data.
    statement.run();
    return [];
  }
}

function readSecureDelete(db: DbLike): number | null {
  try {
    const row = execStatement(db, "PRAGMA secure_delete")[0] as
      | { secure_delete?: unknown }
      | undefined;
    const value = Number(row?.secure_delete);
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

const SEMANTIC_CACHE_EMBEDDING_KEY = "semanticCacheEmbeddingApiKey";

/**
 * Blank the semantic-cache embedding API key wherever databaseSettings keeps it:
 * the `databaseSettings` namespace, the flat `settings` alias, and the nested
 * `settings.databaseSettings.cache` object. Returns how many values were blanked.
 */
function clearEmbeddingKeys(db: DbLike): number {
  let cleared = 0;
  let rows: Array<{ namespace: string; key: string; value: string | null }>;
  try {
    rows = db
      .prepare(
        "SELECT namespace, key, value FROM key_value WHERE " +
          "(namespace = 'databaseSettings' AND key = 'cache.semanticCacheEmbeddingApiKey') OR " +
          "(namespace = 'settings' AND key IN ('semanticCacheEmbeddingApiKey', 'databaseSettings'))"
      )
      .all() as typeof rows;
  } catch {
    return 0;
  }
  const update = db.prepare("UPDATE key_value SET value = ? WHERE namespace = ? AND key = ?");
  for (const row of rows) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(row.value ?? "null");
    } catch {
      parsed = row.value;
    }
    if (row.key === "databaseSettings") {
      const cache =
        parsed && typeof parsed === "object"
          ? ((parsed as Record<string, unknown>).cache as Record<string, unknown> | undefined)
          : undefined;
      const held = cache?.[SEMANTIC_CACHE_EMBEDDING_KEY];
      if (cache && typeof held === "string" && held.trim()) {
        cache[SEMANTIC_CACHE_EMBEDDING_KEY] = "";
        update.run(JSON.stringify(parsed), row.namespace, row.key);
        cleared += 1;
      }
      continue;
    }
    if (typeof parsed === "string" && parsed.trim()) {
      update.run(JSON.stringify(""), row.namespace, row.key);
      cleared += 1;
    }
  }
  return cleared;
}

/** Delete every row of cloud_agent_credentials (Devin, Jules, Codex cloud, Cursor cloud keys). */
function dropAgentCredentials(db: DbLike): number {
  try {
    const counted = db
      .prepare("SELECT COUNT(*) AS n FROM cloud_agent_credentials")
      .all() as Array<{ n: number }>;
    const n = Number(counted[0]?.n ?? 0);
    if (n > 0) db.prepare("DELETE FROM cloud_agent_credentials").run();
    return n;
  } catch {
    return 0;
  }
}

/**
 * Rewrite the file without the pages the scrub freed, then fold the WAL back
 * into it. Never throws: a VACUUM that cannot run (another statement is open)
 * leaves the zeroed cells that secure_delete already wrote.
 */
function vacuumAfterScrub(db: DbLike): boolean {
  try {
    execStatement(db, "VACUUM");
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    console.warn(`[FreeRoute] VACUUM after the secret scrub did not run: ${message}`);
    return false;
  }
  try {
    execStatement(db, "PRAGMA wal_checkpoint(TRUNCATE)");
  } catch {
    // Not in WAL mode, or the checkpoint is blocked; the VACUUM already ran.
  }
  return true;
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
    droppedAgentCredentials: 0,
    clearedEmbeddingKeys: 0,
    vacuumed: false,
  };

  // Zero the bytes of every cell this scrub frees, for this connection only.
  const previousSecureDelete = readSecureDelete(db);
  try {
    execStatement(db, "PRAGMA secure_delete = ON");
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    console.warn(`[FreeRoute] Could not enable secure_delete for the secret scrub: ${message}`);
  }

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

  result.droppedAgentCredentials = dropAgentCredentials(db);
  result.clearedEmbeddingKeys = clearEmbeddingKeys(db);

  const touched =
    result.clearedApiKeys +
    result.strippedProviderData +
    result.disabledClassified +
    result.droppedClientKeys +
    result.droppedAgentCredentials +
    result.clearedEmbeddingKeys;
  if (touched > 0) {
    result.vacuumed = vacuumAfterScrub(db);
    console.warn(
      `[FreeRoute] Removed secrets stored outside OpenVault: ${result.clearedApiKeys} api_key ` +
        `column(s), ${result.strippedProviderData} providerSpecificData secret set(s), ` +
        `${result.disabledClassified} disabled subscription/session connection(s), ` +
        `${result.droppedClientKeys} local client key(s), ${result.droppedAgentCredentials} ` +
        `cloud agent key(s), ${result.clearedEmbeddingKeys} embedding key(s). Provider keys ` +
        `belong in OpenVault (http://127.0.0.1:3010/keys).`
    );
  }
  if (previousSecureDelete !== null && previousSecureDelete !== 1) {
    try {
      execStatement(db, `PRAGMA secure_delete = ${previousSecureDelete}`);
    } catch {
      // Leaving secure_delete on is the safe direction.
    }
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
