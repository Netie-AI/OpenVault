/**
 * Passphrase for this tab's JS heap only.
 *
 * Client navigations keep it. Reload, Lock, and a failed re-unseal drop it.
 * It is not written to browser storage. Windows Hello / passkey unlock is
 * separate and comes later.
 */

let cached = "";

export function rememberSessionPassphrase(passphrase: string): void {
  const value = passphrase.trim();
  if (!value) return;
  cached = value;
}

export function sessionPassphrase(): string {
  return cached;
}

export function sessionUnsealed(): boolean {
  return cached.length > 0;
}

export function clearSessionPassphrase(): void {
  cached = "";
}

/**
 * If this tab already unsealed and the server is sealed again, post the
 * cached passphrase once. The passphrase is not returned.
 */
export async function reopenSealedVault<T extends { sealed?: boolean }>(
  status: T,
  unseal: (passphrase: string) => Promise<T>,
): Promise<T> {
  const phrase = sessionPassphrase();
  if (!status.sealed || !phrase) return status;
  try {
    const next = await unseal(phrase);
    if (next.sealed) clearSessionPassphrase();
    return next;
  } catch (err) {
    clearSessionPassphrase();
    throw err;
  }
}
