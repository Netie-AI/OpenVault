/**
 * SQLite + Drizzle bootstrap. Uses bun:sqlite (built-in) - no native
 * build step, no ABI mismatch when bun upgrades. Self-hosted Zero doesn't
 * need a connection pool; bun:sqlite is synchronous and the IO is local.
 */

import { drizzle } from 'drizzle-orm/bun-sqlite';
import { Database } from 'bun:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { env } from '../env';
import * as schema from './schema';
import { bootstrapSchema } from './bootstrap';

mkdirSync(dirname(env.SQLITE_PATH), { recursive: true });

const sqlite = new Database(env.SQLITE_PATH);
sqlite.exec('PRAGMA journal_mode = WAL;');
sqlite.exec('PRAGMA foreign_keys = ON;');

// Create any missing tables on every boot. Idempotent - pure
// `CREATE TABLE IF NOT EXISTS` so already-bootstrapped DBs incur zero
// cost. Replaces drizzle-kit migrate, which is unworkable for a
// self-hosted app where operators just pull-and-restart.
bootstrapSchema(sqlite);

// Branding used to live in a SQLite singleton row; it's now a JSON file
// on disk (see [lib/branding.ts]). The `branding` table is left in
// place on existing zero.db files - DROP would be destructive - but
// nothing reads it. New deployments never create the table.

export const db = drizzle(sqlite, { schema });
export type DB = typeof db;

export { schema };
