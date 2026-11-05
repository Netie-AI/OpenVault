/**
 * Schema bootstrap - `CREATE TABLE IF NOT EXISTS` for every app-state
 * table at server startup.
 *
 * Why not `drizzle-kit migrate`:
 *   - This is a self-hosted app. Operators don't run `bun db:migrate`
 *     between releases; they pull, restart, and expect it to work.
 *   - There's no destructive migration story here (the schema is
 *     append-only - Zero doesn't own mail data, just session + UI
 *     prefs that are cheap to recreate if the file gets nuked).
 *   - One file < a `./migrations` directory full of SQL files
 *     that have to stay in lockstep with `schema.ts`.
 *
 * If you change `schema.ts`:
 *   - Adding a table → add another `CREATE TABLE IF NOT EXISTS` here.
 *   - Adding a column → add `ALTER TABLE … ADD COLUMN IF NOT EXISTS`
 *     (SQLite 3.35+).
 *   - Renaming/dropping → write a one-shot migration in here gated
 *     by a `PRAGMA user_version` bump.
 *
 * `IF NOT EXISTS` everywhere keeps this idempotent so it runs at every
 * boot with zero observable cost on already-bootstrapped DBs.
 */

import type { Database } from 'bun:sqlite';

const STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    name TEXT,
    encrypted_password BLOB NOT NULL,
    imap_host TEXT NOT NULL,
    imap_port INTEGER NOT NULL,
    smtp_host TEXT NOT NULL,
    smtp_port INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
  )`,
  `CREATE INDEX IF NOT EXISTS idx_session_email ON session(email)`,
  `CREATE INDEX IF NOT EXISTS idx_session_expires_at ON session(expires_at)`,

  `CREATE TABLE IF NOT EXISTS user_settings (
    email TEXT PRIMARY KEY,
    settings TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
  )`,

  `CREATE TABLE IF NOT EXISTS user_hotkeys (
    email TEXT NOT NULL,
    shortcut_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (email, shortcut_id)
  )`,

  `CREATE TABLE IF NOT EXISTS email_template (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    name TEXT NOT NULL,
    subject TEXT,
    body TEXT,
    "to" TEXT,
    cc TEXT,
    bcc TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
  )`,
  `CREATE INDEX IF NOT EXISTS idx_email_template_email ON email_template(email)`,

  `CREATE TABLE IF NOT EXISTS cookie_consent (
    email TEXT PRIMARY KEY,
    consent TEXT NOT NULL,
    updated_at INTEGER NOT NULL
  )`,
];

export function bootstrapSchema(sqlite: Database): void {
  for (const sql of STATEMENTS) {
    sqlite.exec(sql);
  }
}
