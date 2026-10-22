/**
 * This tab's unseal, in the JS heap only.
 *
 * A passphrase unseal keeps the passphrase. A Windows Hello / passkey unseal
 * keeps only that this tab used the existing WebAuthn unseal. Client
 * navigations keep it. Reload, Lock, and a failed re-unseal drop it.
 * Neither is written to browser storage.
 */

type SessionKind = "none" | "passphrase" | "webauthn";

let kind: SessionKind = "none";
let cached = "";

export function rememberSessionPassphrase(passphrase: string): void {
  const value = passphrase.trim();
  if (!value) return;
  cached = value;
  kind = "passphrase";
}

/** Same session slot as the passphrase cache. Stores no PRF and no passphrase. */
export function rememberSessionWebAuthn(): void {
  cached = "";
  kind = "webauthn";
}

export function sessionUnlockKind(): SessionKind {
  return kind;
}

export function sessionPassphrase(): string {
  return cached;
}

export function sessionUnsealed(): boolean {
  return kind === "webauthn" || cached.length > 0;
}

export function clearSessionPassphrase(): void {
  cached = "";
  kind = "none";
}

/**
 * If this tab already unsealed and the server is sealed again, reopen once.
 * Windows Hello wins when this session used it, and when the vault reports
 * webauthn_registered and no passphrase is cached. The passphrase cache is
 * the fallback. The passphrase is not returned.
 */
export async function reopenSealedVault<
  T extends { sealed?: boolean; webauthn_registered?: boolean },
>(
  status: T,
  unseal: (passphrase: string) => Promise<T>,
  unsealWithPasskey?: () => Promise<T>,
): Promise<T> {
  if (!status.sealed) return status;

  if (kind === "webauthn") {
    if (status.webauthn_registered === true && unsealWithPasskey) {
      try {
        const next = await unsealWithPasskey();
        if (next.sealed) clearSessionPassphrase();
        else rememberSessionWebAuthn();
        return next;
      } catch (err) {
        clearSessionPassphrase();
        throw err;
      }
    }
    clearSessionPassphrase();
  }

  if (
    kind !== "passphrase" &&
    status.webauthn_registered === true &&
    unsealWithPasskey &&
    !sessionPassphrase()
  ) {
    try {
      const next = await unsealWithPasskey();
      if (!next.sealed) rememberSessionWebAuthn();
      return next;
    } catch {
      return status;
    }
  }

  const phrase = sessionPassphrase();
  if (!phrase) return status;
  try {
    const next = await unseal(phrase);
    if (next.sealed) clearSessionPassphrase();
    return next;
  } catch (err) {
    clearSessionPassphrase();
    throw err;
  }
}
