import { getDbInstance } from "@/lib/db/core";
import type { AgentCredentials } from "@/lib/cloudAgent/baseAgent";
import { KeysManagedByOpenVaultError, resolveProviderApiKey } from "@/lib/netie/keyvault";

// FreeRoute: cloud agent API keys (Jules, Devin, Codex cloud, Cursor cloud) live
// only in OpenVault's KeyVault. They are read over loopback at request time and
// never stored here. The `cloud_agent_credentials` table (migration
// `061_cloud_agent_credentials.sql`) is no longer written or read: saving answers
// 501 keys_managed_by_openvault, and src/lib/netie/scrubLocalSecrets.ts empties
// the table from any imported or older database.

/** The cloud agent providers whose key is looked up in OpenVault under the provider id. */
export const CLOUD_AGENT_PROVIDER_IDS = ["jules", "devin", "codex-cloud", "cursor-cloud"] as const;

/** Mask API key for display — show last 4 chars only */
export function maskApiKey(key: string): string {
  if (!key || key.length <= 4) return "****";
  return "****" + key.slice(-4);
}

/**
 * Credentials for a cloud agent provider from OpenVault, or null when OpenVault
 * holds no pooled key for it. Throws the KeyVault 503 errors (OpenVault down,
 * sealed, admin token missing or rejected) rather than falling back to anything
 * stored locally.
 */
export async function getCloudAgentCredentialFromVault(
  providerId: string
): Promise<AgentCredentials | null> {
  const apiKey = await resolveProviderApiKey(providerId);
  return apiKey ? { apiKey } : null;
}

/** Providers OpenVault holds a key for. Never carries key material, not even a masked tail. */
export async function listCloudAgentCredentials(): Promise<
  Array<{ providerId: string; apiKey: string; baseUrl: string | null; updatedAt: string | null }>
> {
  const rows: Array<{
    providerId: string;
    apiKey: string;
    baseUrl: string | null;
    updatedAt: string | null;
  }> = [];
  for (const providerId of CLOUD_AGENT_PROVIDER_IDS) {
    if (await resolveProviderApiKey(providerId)) {
      rows.push({ providerId, apiKey: "managed in OpenVault", baseUrl: null, updatedAt: null });
    }
  }
  return rows;
}

/** Always refuses: a cloud agent key is added in OpenVault, never saved here. */
export function saveCloudAgentCredential(
  _providerId: string,
  _apiKey: string,
  _baseUrl?: string
): never {
  throw new KeysManagedByOpenVaultError(["apiKey"]);
}

/** Delete credentials for a provider (clears a row left in the local table by an older build). */
export function deleteCloudAgentCredential(providerId: string): void {
  const db = getDbInstance();
  db.prepare("DELETE FROM cloud_agent_credentials WHERE provider_id = ?").run(providerId);
}
