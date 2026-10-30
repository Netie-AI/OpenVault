import { decryptCredential } from "./encryption.mjs";
import { KeysManagedByOpenVaultError } from "./openvault-keys.mjs";

const REQUIRED_PROVIDER_COLUMNS = [
  ["auth_type", "TEXT"],
  ["name", "TEXT"],
  ["email", "TEXT"],
  ["priority", "INTEGER DEFAULT 0"],
  ["is_active", "INTEGER DEFAULT 1"],
  ["access_token", "TEXT"],
  ["refresh_token", "TEXT"],
  ["expires_at", "TEXT"],
  ["token_expires_at", "TEXT"],
  ["scope", "TEXT"],
  ["project_id", "TEXT"],
  ["test_status", "TEXT"],
  ["error_code", "TEXT"],
  ["last_error", "TEXT"],
  ["last_error_at", "TEXT"],
  ["last_error_type", "TEXT"],
  ["last_error_source", "TEXT"],
  ["backoff_level", "INTEGER DEFAULT 0"],
  ["rate_limited_until", "TEXT"],
  ["health_check_interval", "INTEGER"],
  ["last_health_check_at", "TEXT"],
  ["last_tested", "TEXT"],
  ["api_key", "TEXT"],
  ["id_token", "TEXT"],
  ["provider_specific_data", "TEXT"],
  ["expires_in", "INTEGER"],
  ["display_name", "TEXT"],
  ["global_priority", "INTEGER"],
  ["default_model", "TEXT"],
  ["token_type", "TEXT"],
  ["consecutive_use_count", "INTEGER DEFAULT 0"],
  ["rate_limit_protection", "INTEGER DEFAULT 0"],
  ["last_used_at", "TEXT"],
  ['"group"', "TEXT"],
  ["max_concurrent", "INTEGER"],
  ["created_at", "TEXT"],
  ["updated_at", "TEXT"],
];

function ensureProviderColumns(db) {
  const existingColumns = new Set(
    db
      .prepare("PRAGMA table_info(provider_connections)")
      .all()
      .map((column) => column.name)
  );

  const missingColumns = REQUIRED_PROVIDER_COLUMNS.filter(([name]) => {
    const normalizedName = name.replaceAll('"', "");
    return !existingColumns.has(normalizedName);
  });

  if (missingColumns.length === 0) return;

  db.transaction(() => {
    for (const [name, type] of missingColumns) {
      db.prepare(`ALTER TABLE provider_connections ADD COLUMN ${name} ${type}`).run();
    }
  })();
}

export function ensureProviderSchema(db) {
  db.prepare(
    `CREATE TABLE IF NOT EXISTS provider_connections (
      id TEXT PRIMARY KEY,
      provider TEXT NOT NULL,
      auth_type TEXT,
      name TEXT,
      email TEXT,
      priority INTEGER DEFAULT 0,
      is_active INTEGER DEFAULT 1,
      access_token TEXT,
      refresh_token TEXT,
      expires_at TEXT,
      token_expires_at TEXT,
      scope TEXT,
      project_id TEXT,
      test_status TEXT,
      error_code TEXT,
      last_error TEXT,
      last_error_at TEXT,
      last_error_type TEXT,
      last_error_source TEXT,
      backoff_level INTEGER DEFAULT 0,
      rate_limited_until TEXT,
      health_check_interval INTEGER,
      last_health_check_at TEXT,
      last_tested TEXT,
      api_key TEXT,
      id_token TEXT,
      provider_specific_data TEXT,
      expires_in INTEGER,
      display_name TEXT,
      global_priority INTEGER,
      default_model TEXT,
      token_type TEXT,
      consecutive_use_count INTEGER DEFAULT 0,
      rate_limit_protection INTEGER DEFAULT 0,
      last_used_at TEXT,
      "group" TEXT,
      max_concurrent INTEGER,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )`
  ).run();
  ensureProviderColumns(db);
  db.prepare("CREATE INDEX IF NOT EXISTS idx_pc_provider ON provider_connections(provider)").run();
  db.prepare("CREATE INDEX IF NOT EXISTS idx_pc_active ON provider_connections(is_active)").run();
  db.prepare(
    "CREATE INDEX IF NOT EXISTS idx_pc_priority ON provider_connections(provider, priority)"
  ).run();
}

function parseJsonObject(value) {
  if (!value || typeof value !== "string") return undefined;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function rowToConnection(row) {
  return {
    id: row.id,
    provider: row.provider,
    authType: row.auth_type || "oauth",
    name: row.name || row.display_name || row.email || row.provider,
    email: row.email || null,
    priority: row.priority || 0,
    isActive: row.is_active !== 0,
    apiKey: row.api_key || null,
    accessToken: row.access_token || null,
    refreshToken: row.refresh_token || null,
    idToken: row.id_token || null,
    providerSpecificData: parseJsonObject(row.provider_specific_data),
    testStatus: row.test_status || "unknown",
    defaultModel: row.default_model || null,
    lastTested: row.last_tested || null,
    lastError: row.last_error || null,
    updatedAt: row.updated_at || null,
    createdAt: row.created_at || null,
  };
}

export function listProviderConnections(db) {
  ensureProviderSchema(db);
  return db
    .prepare(
      `SELECT * FROM provider_connections
       ORDER BY provider ASC, priority ASC, updated_at DESC`
    )
    .all()
    .map(rowToConnection);
}

export function findProviderConnection(db, selector) {
  const normalized = String(selector || "")
    .trim()
    .toLowerCase();
  if (!normalized) return null;

  const connections = listProviderConnections(db);
  return (
    connections.find((connection) => connection.id.toLowerCase() === normalized) ||
    connections.find((connection) => connection.id.toLowerCase().startsWith(normalized)) ||
    connections.find((connection) => String(connection.name || "").toLowerCase() === normalized) ||
    connections.find((connection) => connection.provider.toLowerCase() === normalized) ||
    null
  );
}

export function getProviderApiKey(connection) {
  if (connection.authType !== "apikey") {
    throw new Error(`Connection ${connection.name} is not an API-key provider.`);
  }
  if (!connection.apiKey) {
    throw new Error(`Connection ${connection.name} has no API key configured.`);
  }
  return decryptCredential(connection.apiKey);
}

/**
 * FreeRoute never stores a provider key (see openvault-keys.mjs), so this always
 * throws KeysManagedByOpenVaultError. Kept as an export so a caller that still
 * imports it fails loudly with the named error instead of a missing-export crash.
 */
export function upsertApiKeyProviderConnection(_db, _input) {
  throw new KeysManagedByOpenVaultError();
}

export function removeProviderConnectionByProvider(db, provider) {
  ensureProviderSchema(db);
  const result = db
    .prepare(
      "DELETE FROM provider_connections WHERE provider = ? AND (auth_type = 'apikey' OR (api_key IS NOT NULL AND api_key != ''))"
    )
    .run(provider);
  return result.changes;
}

/**
 * FreeRoute never stores a provider key (see openvault-keys.mjs), so this always
 * throws KeysManagedByOpenVaultError.
 */
export function updateProviderApiKey(_db, _connectionId, _encryptedKey) {
  throw new KeysManagedByOpenVaultError();
}

export function updateProviderTestResult(db, connectionId, result) {
  ensureProviderSchema(db);
  const now = new Date().toISOString();
  const valid = result?.valid === true;

  db.prepare(
    `UPDATE provider_connections SET
      test_status = @testStatus,
      last_error = @lastError,
      last_error_at = @lastErrorAt,
      last_error_type = @lastErrorType,
      last_error_source = @lastErrorSource,
      error_code = @errorCode,
      last_tested = @lastTested,
      updated_at = @updatedAt
    WHERE id = @id`
  ).run({
    id: connectionId,
    testStatus: valid ? "active" : "error",
    lastError: valid ? null : result?.error || "Provider test failed",
    lastErrorAt: valid ? null : now,
    lastErrorType: valid ? null : "connection_test_failed",
    lastErrorSource: valid ? null : "upstream",
    errorCode: valid ? null : result?.statusCode || null,
    lastTested: now,
    updatedAt: now,
  });

  return {
    ...result,
    testedAt: now,
  };
}
