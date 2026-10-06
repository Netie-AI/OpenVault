/**
 * `enc1:` envelope decrypt — matches apps/api/src/lib/credential-encryption.ts.
 *
 * Lives in @repo/adapters so destination factories can decrypt their own
 * credentials at construction time without an inbound dep from apps/api.
 * Key derivation uses BETTER_AUTH_SECRET (same SHA-256 key the API
 * layer uses), so a value encrypted by the API decrypts here.
 *
 * Encrypted form: "enc1:" + base64(iv16 || authTag16 || ciphertext)
 * Values without the prefix are returned verbatim.
 */

import { createDecipheriv, createHash } from "node:crypto";

const PREFIX = "enc1:" as const;
const ALGORITHM = "aes-256-gcm" as const;
const IV_LENGTH = 16;
const AUTH_TAG_LENGTH = 16;

// The AES key secret. The app injects its already-resolved BETTER_AUTH_SECRET
// once at boot (setBackupCredentialSecret) so this low-level package derives the
// SAME key the API's encryptor used — including the app's config default —
// instead of independently reading process.env (which zod's `.default()` never
// populates, causing encrypt/decrypt to disagree). process.env is only a
// fallback for entrypoints that don't inject (e.g. isolated tests).
let injectedSecret: string | undefined;

/** Inject the API-resolved BETTER_AUTH_SECRET. Call once at app boot. */
export function setBackupCredentialSecret(secret: string | undefined): void {
  injectedSecret = secret || undefined;
}

function deriveKey(): Buffer {
  const secret = injectedSecret ?? process.env.BETTER_AUTH_SECRET;
  if (!secret) {
    throw new Error(
      "BETTER_AUTH_SECRET is not set — cannot decrypt backup destination credentials.",
    );
  }
  return createHash("sha256").update(secret).digest();
}

export function decryptCredential(stored: string | null | undefined): string | undefined {
  if (stored == null || stored === "") return undefined;
  if (!stored.startsWith(PREFIX)) return stored;

  const packed = Buffer.from(stored.slice(PREFIX.length), "base64");
  if (packed.length < IV_LENGTH + AUTH_TAG_LENGTH) {
    throw new Error("Invalid credential ciphertext: too short");
  }
  const iv = packed.subarray(0, IV_LENGTH);
  const authTag = packed.subarray(IV_LENGTH, IV_LENGTH + AUTH_TAG_LENGTH);
  const ciphertext = packed.subarray(IV_LENGTH + AUTH_TAG_LENGTH);

  const decipher = createDecipheriv(ALGORITHM, deriveKey(), iv);
  decipher.setAuthTag(authTag);
  return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString("utf8");
}
