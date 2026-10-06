/**
 * AES-256-GCM encryption for at-rest IMAP credentials in the session
 * table. Key comes from SESSION_ENCRYPTION_KEY (hex-encoded 32 bytes).
 *
 * Format on disk: [12-byte IV | ciphertext | 16-byte GCM tag].
 *
 * Use only for the IMAP password - never for anything else. Loss of
 * the key invalidates every session (the next sign-in re-issues).
 */

import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import { env } from '../env';

const ALGO = 'aes-256-gcm';
const IV_LEN = 12;
const TAG_LEN = 16;

function getKey(): Buffer {
  const hex = env.SESSION_ENCRYPTION_KEY;
  if (!/^[0-9a-fA-F]{64}$/.test(hex)) {
    throw new Error(
      'SESSION_ENCRYPTION_KEY must be a 64-character hex string (32 bytes). ' +
        'Generate one with: `openssl rand -hex 32`',
    );
  }
  return Buffer.from(hex, 'hex');
}

export function encryptSecret(plaintext: string): Buffer {
  const key = getKey();
  const iv = randomBytes(IV_LEN);
  const cipher = createCipheriv(ALGO, key, iv);
  const ciphertext = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, ciphertext, tag]);
}

export function decryptSecret(payload: Buffer): string {
  if (payload.length < IV_LEN + TAG_LEN) {
    throw new Error('Encrypted payload too short');
  }
  const key = getKey();
  const iv = payload.subarray(0, IV_LEN);
  const tag = payload.subarray(payload.length - TAG_LEN);
  const ciphertext = payload.subarray(IV_LEN, payload.length - TAG_LEN);
  const decipher = createDecipheriv(ALGO, key, iv);
  decipher.setAuthTag(tag);
  const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  return plaintext.toString('utf8');
}
