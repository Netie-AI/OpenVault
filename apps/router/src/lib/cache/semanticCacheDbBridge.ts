import { getDatabaseSettings } from "@/lib/db/databaseSettings";
import { getDbInstance } from "@/lib/db/core";
import { decryptConnectionFields } from "@/lib/db/encryption";
import { registerSemanticCacheConfigResolver } from "@omniroute/open-sse/config/semanticCacheConfig.ts";

let registered = false;

export function resolveProviderConnectionDetails(provider: string): {
  baseUrl?: string;
  apiKey?: string;
} {
  if (!provider) return {};
  try {
    const db = getDbInstance();
    const row = db
      .prepare(
        "SELECT * FROM provider_connections WHERE provider = ? AND is_active != 0 ORDER BY priority ASC, id ASC LIMIT 1"
      )
      .get(provider) as Record<string, unknown> | undefined;

    if (!row) return {};
    const decrypted = decryptConnectionFields(row);
    let baseUrl: string | undefined;
    if (decrypted.provider_specific_data) {
      try {
        const parsed =
          typeof decrypted.provider_specific_data === "string"
            ? JSON.parse(decrypted.provider_specific_data)
            : decrypted.provider_specific_data;
        if (typeof parsed?.baseUrl === "string" && parsed.baseUrl.trim()) {
          baseUrl = parsed.baseUrl.trim();
        }
      } catch {
        // Ignore parse error
      }
    }
    // FreeRoute: the api_key column never holds a provider key (keys live in
    // OpenVault), and this resolver is synchronous, so it cannot ask
    // OpenVault. No key is returned; callers that can await use
    // resolveConnectionApiKey (src/lib/netie/providerGuards.ts).
    return { baseUrl, apiKey: undefined };
  } catch {
    return {};
  }
}

export function ensureSemanticCacheDbBridge(): void {
  if (registered) return;
  registered = true;
  registerSemanticCacheConfigResolver(() => {
    try {
      const s = getDatabaseSettings().cache;
      if (!s) return null;

      const conn = s.semanticCacheEmbeddingProvider
        ? resolveProviderConnectionDetails(s.semanticCacheEmbeddingProvider)
        : {};

      const embeddingBaseUrl = s.semanticCacheEmbeddingBaseUrl || conn.baseUrl;
      const embeddingApiKey = s.semanticCacheEmbeddingApiKey || conn.apiKey;

      return {
        // The vector layer is opt-in (#14159): it only runs when the operator turned
        // on BOTH the master semantic-cache toggle and the vector-layer toggle. With
        // it off, chatCore behaves exactly like the legacy SQLite exact-match cache.
        enabled: s.semanticCacheEnabled !== false && s.semanticCacheVectorEnabled === true,
        backend: s.semanticCacheBackend,
        similarityThreshold: s.semanticCacheThreshold,
        ttlMs: s.semanticCacheTTL,
        maxEntries: s.semanticCacheMaxSize,
        embeddingProvider: s.semanticCacheEmbeddingProvider,
        embeddingModel: s.semanticCacheEmbeddingModel,
        embeddingDimension: s.semanticCacheEmbeddingDimension,
        embeddingBaseUrl,
        embeddingApiKey,
        redisUrl: s.semanticCacheRedisUrl,
        redisPrefix: s.semanticCacheRedisPrefix,
        requireZeroTemperature: s.semanticCacheRequireZeroTemp,
      };
    } catch {
      return null;
    }
  });
}
