/**
 * Session lookup, create, and delete helpers.
 *
 * A session in Zero is the *identity* - the mailbox itself. There is
 * no separate user table. A row in `session` represents one active
 * sign-in: it holds the encrypted IMAP password and the IMAP/SMTP
 * coordinates so we can open connections per request without asking
 * the user to re-authenticate.
 *
 * `getSession()` is what middleware calls; it returns the row plus a
 * decrypted password ready for `withImap` / `sendMail`.
 */

import { eq } from 'drizzle-orm';
import { nanoid } from 'nanoid';
import { db, schema } from '../db';
import { encryptSecret, decryptSecret } from './crypto';
import { env } from '../env';

export interface SessionContext {
  sessionId: string;
  email: string;
  name: string | null;
  password: string;
  imapHost: string;
  imapPort: number;
  smtpHost: string;
  smtpPort: number;
  expiresAt: Date;
}

export async function createSession(opts: {
  email: string;
  name: string | null;
  password: string;
  imapHost: string;
  imapPort: number;
  smtpHost: string;
  smtpPort: number;
}): Promise<{ id: string; expiresAt: Date }> {
  const id = nanoid(40);
  const now = new Date();
  const expiresAt = new Date(now.getTime() + env.SESSION_TTL_SECONDS * 1000);
  await db.insert(schema.session).values({
    id,
    email: opts.email.toLowerCase(),
    name: opts.name,
    encryptedPassword: encryptSecret(opts.password),
    imapHost: opts.imapHost,
    imapPort: opts.imapPort,
    smtpHost: opts.smtpHost,
    smtpPort: opts.smtpPort,
    createdAt: now,
    expiresAt,
  });
  return { id, expiresAt };
}

export async function getSession(sessionId: string): Promise<SessionContext | null> {
  const row = await db.query.session.findFirst({
    where: eq(schema.session.id, sessionId),
  });
  if (!row) return null;
  if (row.expiresAt.getTime() < Date.now()) {
    await deleteSession(sessionId);
    return null;
  }
  return {
    sessionId: row.id,
    email: row.email,
    name: row.name,
    password: decryptSecret(row.encryptedPassword as Buffer),
    imapHost: row.imapHost,
    imapPort: row.imapPort,
    smtpHost: row.smtpHost,
    smtpPort: row.smtpPort,
    expiresAt: row.expiresAt,
  };
}

export async function deleteSession(sessionId: string): Promise<void> {
  await db.delete(schema.session).where(eq(schema.session.id, sessionId));
}

/**
 * Derives IMAP/SMTP coordinates for an email address. If the env var
 * overrides are set, use them - otherwise guess `mail.<domain>`.
 *
 * This is the convention iRedMail installs out of the box, and is the
 * shape openship's mail panel provisions; for other setups, the
 * sign-in endpoint accepts host/port overrides.
 */
export function defaultMailHosts(email: string): {
  imapHost: string;
  imapPort: number;
  smtpHost: string;
  smtpPort: number;
} {
  const domain = email.split('@')[1] ?? '';
  return {
    imapHost: env.DEFAULT_IMAP_HOST ?? `mail.${domain}`,
    imapPort: env.DEFAULT_IMAP_PORT,
    smtpHost: env.DEFAULT_SMTP_HOST ?? `mail.${domain}`,
    smtpPort: env.DEFAULT_SMTP_PORT,
  };
}
