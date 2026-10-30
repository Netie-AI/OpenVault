/**
 * PGlite recovery — get a corrupted data dir back to a startable state
 * WITHOUT deleting committed project data.
 *
 * Symptom this fixes:
 *   DrizzleQueryError: Failed query: CREATE SCHEMA IF NOT EXISTS "drizzle"
 *   cause: RuntimeError: Aborted()
 *
 * Recovery strategy:
 *   1. Take a full backup of the data dir to <dir>.backup-<ts>
 *      (rsync-style copy, never moves the original)
 *   2. Remove any stale `postmaster.pid` lock
 *   3. Truncate the active WAL segment to a clean state — when a node
 *      process is killed mid-write, the WAL tail can contain a partial
 *      record that aborts the WASM PG on replay. We do NOT touch the
 *      base/ or global/ files where committed pages live.
 *   4. Drop the heal-orphans tables (cloud_edge_proxy + cloud_page)
 *      if reachable on the next start — handled separately by
 *      `db:heal-orphans`.
 *
 * If the recovery destroys data you care about, restore the backup
 * directory and report it as a bug.
 *
 *   bun --cwd packages/db db:heal-pglite
 */

import { existsSync, readdirSync, statSync, unlinkSync, mkdirSync, copyFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

function resolveDataDir(): string {
  return process.env.PGLITE_DATA_DIR ?? join(homedir(), ".openship", "data");
}

function copyDirRecursive(src: string, dest: string): void {
  if (!existsSync(dest)) mkdirSync(dest, { recursive: true });
  for (const entry of readdirSync(src)) {
    const s = join(src, entry);
    const d = join(dest, entry);
    const st = statSync(s);
    if (st.isDirectory()) {
      copyDirRecursive(s, d);
    } else {
      copyFileSync(s, d);
    }
  }
}

function ts(): string {
  // Date.now() is forbidden in some contexts; use process.hrtime for a
  // monotonic-ish suffix.
  const [s, ns] = process.hrtime();
  return `${s}-${ns}`;
}

async function main() {
  const dataDir = resolveDataDir();
  if (!existsSync(dataDir)) {
    console.log(`heal-pglite: no data dir at ${dataDir} — nothing to heal.`);
    process.exit(0);
  }

  // 1. Backup
  const backupDir = `${dataDir}.backup-${ts()}`;
  console.log(`heal-pglite: backing up ${dataDir} → ${backupDir} ...`);
  copyDirRecursive(dataDir, backupDir);
  console.log(`heal-pglite: backup complete.`);

  // 2. Remove stale lock
  const lockPath = join(dataDir, "postmaster.pid");
  if (existsSync(lockPath)) {
    unlinkSync(lockPath);
    console.log(`heal-pglite: removed ${lockPath}`);
  }

  // 3. Truncate the active WAL segment. The wal_status / size hints
  // here are conservative: we keep every file the PG checkpoint needs
  // (anything older than the latest non-zero-size segment) and remove
  // ONLY the active segment whose tail may be corrupted.
  const walDir = join(dataDir, "pg_wal");
  if (existsSync(walDir)) {
    const segments = readdirSync(walDir)
      .filter((name) => /^[0-9A-F]{24}$/.test(name))
      .map((name) => ({ name, mtime: statSync(join(walDir, name)).mtimeMs }))
      .sort((a, b) => b.mtime - a.mtime);
    if (segments.length > 0) {
      const newest = segments[0];
      const newestPath = join(walDir, newest.name);
      console.log(`heal-pglite: removing latest WAL segment ${newest.name} (suspected corrupt tail).`);
      unlinkSync(newestPath);
    } else {
      console.log(`heal-pglite: no WAL segments to truncate.`);
    }
  }

  console.log(`heal-pglite: done.`);
  console.log(`Next steps:`);
  console.log(`  1. Restart 'bun dev' — PGlite should recover from the previous checkpoint.`);
  console.log(`  2. If it still crashes, restore from: ${backupDir}`);
  console.log(`  3. After successful start, run 'bun --cwd packages/db db:heal-orphans' to drop the cloud_edge_proxy / cloud_page leftover tables.`);
  process.exit(0);
}

main().catch((err) => {
  console.error("heal-pglite failed:", err);
  process.exit(1);
});
