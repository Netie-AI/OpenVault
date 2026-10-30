// Copyright (c) 2026 Netie AI. MIT.
//
// FreeRoute does not keep provider keys. They live only in OpenVault's KeyVault
// and are read over loopback at request time (src/lib/netie/keyvault.ts). Every
// CLI command that used to write a provider key into FreeRoute's own SQLite file
// (`keys add`, `setup --add-provider`, `providers rotate`) refuses with this
// message and a non-zero exit instead, and does not fall back to a local write
// when the server answers 501 keys_managed_by_openvault.

export const KEYS_MANAGED_BY_OPENVAULT_CODE = "keys_managed_by_openvault";
export const OPENVAULT_KEYS_URL = "http://127.0.0.1:3010/keys";
export const KEYS_MANAGED_BY_OPENVAULT_MESSAGE = `Provider keys are stored in OpenVault. Add them at ${OPENVAULT_KEYS_URL}.`;

export class KeysManagedByOpenVaultError extends Error {
  constructor() {
    super(KEYS_MANAGED_BY_OPENVAULT_MESSAGE);
    this.name = "KeysManagedByOpenVaultError";
    this.code = KEYS_MANAGED_BY_OPENVAULT_CODE;
    this.status = 501;
  }
}

/** Print the refusal on stderr and return the exit code the command must return. */
export function refuseLocalKeyWrite() {
  console.error(`${KEYS_MANAGED_BY_OPENVAULT_CODE}: ${KEYS_MANAGED_BY_OPENVAULT_MESSAGE}`);
  return 1;
}
