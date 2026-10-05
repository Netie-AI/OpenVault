/**
 * Sealed-vault gate copy. The bar reads GET /api/vault/status; the gate
 * posts the passphrase to POST /api/vault/unseal.
 */

export const VAULT_STATUS_PATH = "/api/vault/status";

export const VAULT_UNSEAL_PATH = "/api/vault/unseal";

export const SEALED_GATE_TITLE = "Vault is sealed";

export const SEALED_GATE_BODY =
  "Enter the passphrase once. It stays open for this app session until you Lock. After the vault opens, you can add keys here.";

export type SealChrome = "sealed" | "open" | "unknown";

export function sealChrome(sealed: boolean | undefined): SealChrome {
  if (sealed === true) return "sealed";
  if (sealed === false) return "open";
  return "unknown";
}

/**
 * The gate blocks key add while the vault is sealed, or while status itself
 * is 401 (no admin session), until the user dismisses it.
 * A passphrase already cached for this app session keeps the gate closed.
 * A 401 still opens it so the admin token can be entered.
 */
export function sealedGateOpen(
  sealed: boolean | undefined,
  dismissed: boolean,
  unauthorized = false,
  sessionOpen = false,
): boolean {
  if (unauthorized) return !dismissed;
  if (sessionOpen) return false;
  return sealed === true && !dismissed;
}
