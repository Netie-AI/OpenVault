/**
 * Sealed-vault gate copy. The bar reads GET /api/vault/status; the gate
 * posts the passphrase to POST /api/vault/unseal.
 */

export const VAULT_STATUS_PATH = "/api/vault/status";

export const VAULT_UNSEAL_PATH = "/api/vault/unseal";

export const SEALED_GATE_TITLE = "Vault is sealed";

export const SEALED_GATE_BODY =
  "Enter the passphrase once. After the vault opens, you can add keys here.";

export type SealChrome = "sealed" | "open" | "unknown";

export function sealChrome(sealed: boolean | undefined): SealChrome {
  if (sealed === true) return "sealed";
  if (sealed === false) return "open";
  return "unknown";
}

/**
 * The gate blocks key add while the vault is sealed, or while status itself
 * is 401 (no admin session), until the user dismisses it.
 */
export function sealedGateOpen(
  sealed: boolean | undefined,
  dismissed: boolean,
  unauthorized = false,
): boolean {
  return (sealed === true || unauthorized) && !dismissed;
}
