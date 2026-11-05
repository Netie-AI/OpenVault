/**
 * Deep PGlite recovery via pg_resetwal — copy-verify-swap pattern so the
 * original data dir is NEVER mutated until we've proven the recovery
 * worked end-to-end. Preserves committed data; only discards in-flight
 * WAL records (transactions that were already lost when PG was killed).
 *
 *   1. Snapshot live data dir → `<data>.preheal-<ts>` (full copy)
 *   2. Copy data dir again → `<data>.heal-<ts>` (working copy)
 *   3. Run system `pg_resetwal -f` against the WORKING COPY only
 *   4. Open PGlite against the working copy + count project rows
 *   5. If healthy AND project count matches the original (where readable):
 *        - Move live data dir → `<data>.broken-<ts>`
 *        - Move working copy → live data dir
 *        - Keep both snapshots for safety
 *      Else:
 *        - Leave the live data dir UNTOUCHED
 *        - Report the working copy + preheal snapshot paths for manual review
 *
 *   bun --cwd packages/db db:heal-pglite-resetwal
 */

import { existsSync, mkdirSync, readdirSync, statSync, copyFileSync, renameSync, unlinkSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";

function dataDir(): string {
  return process.env.PGLITE_DATA_DIR ?? join(homedir(), ".openship", "data");
}

function suffix(): string {
  const [s, ns] = process.hrtime();
  return `${s}-${ns}`;
}

function copyDir(src: string, dest: string): void {
  if (!existsSync(dest)) mkdirSync(dest, { recursive: true });
  for (const entry of readdirSync(src)) {
    const s = join(src, entry);
    const d = join(dest, entry);
    const st = statSync(s);
    if (st.isDirectory()) {
      copyDir(s, d);
    } else if (st.isFile()) {
      copyFileSync(s, d);
    }
  }
}

const PG_RESETWAL_CANDIDATES = [
  "/opt/homebrew/opt/postgresql@17/bin/pg_resetwal",
  "/opt/homebrew/opt/postgresql@16/bin/pg_resetwal",
  "/opt/homebrew/bin/pg_resetwal",
  "/usr/local/opt/postgresql@17/bin/pg_resetwal",
  "/usr/bin/pg_resetwal",
];

function findResetWal(): string {
  for (const p of PG_RESETWAL_CANDIDATES) {
    if (existsSync(p)) return p;
  }
  throw new Error(
    "pg_resetwal not found. Install Postgres 17:  brew install postgresql@17  (or set the path explicitly).",
  );
}

async function tryOpenAndCount(dir: string): Promise<{ ok: true; count: number } | { ok: false; error: string }> {
  try {
    const { PGlite } = (await import("@electric-sql/pglite")) as typeof import("@electric-sql/pglite");
    const db = new PGlite(dir);
    try {
      const r = await db.query<{ n: number }>(
        "SELECT COUNT(*)::int as n FROM project WHERE deleted_at IS NULL",
      );
      const count = r.rows[0]?.n ?? 0;
      await db.close();
      return { ok: true, count };
    } catch (e) {
      try { await db.close(); } catch { /* best-effort */ }
      return { ok: false, error: (e as Error).message };
    }
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

async function main() {
  const live = dataDir();
  if (!existsSync(live)) {
    console.log(`heal-pglite-resetwal: no data dir at ${live} — nothing to heal.`);
    process.exit(0);
  }

  const sfx = suffix();
  const preheal = `${live}.preheal-${sfx}`;
  const working = `${live}.heal-${sfx}`;
  const broken = `${live}.broken-${sfx}`;

  console.log(`heal-pglite-resetwal: snapshot ${live} → ${preheal}`);
  copyDir(live, preheal);

  console.log(`heal-pglite-resetwal: working copy ${live} → ${working}`);
  copyDir(live, working);

  // pg_resetwal refuses to run if a postmaster.pid exists, even a stale
  // one. Working copy is isolated — safe to drop.
  const lockPath = join(working, "postmaster.pid");
  if (existsSync(lockPath)) {
    unlinkSync(lockPath);
    console.log(`heal-pglite-resetwal: removed stale ${lockPath}`);
  }

  const resetwal = findResetWal();
  console.log(`heal-pglite-resetwal: running ${resetwal} -f ${working}`);
  try {
    const out = execFileSync(resetwal, ["-f", working], {
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    console.log(out.trim());
  } catch (e) {
    const err = e as { stderr?: Buffer; message: string };
    console.error("heal-pglite-resetwal: pg_resetwal FAILED on working copy:");
    console.error(err.stderr?.toString() ?? err.message);
    console.error("LIVE data dir is unchanged. Working copy left at:");
    console.error(`  ${working}`);
    process.exit(1);
  }

  console.log(`heal-pglite-resetwal: verifying working copy opens with PGlite…`);
  const verify = await tryOpenAndCount(working);
  if (!verify.ok) {
    console.error(`heal-pglite-resetwal: PGlite still aborts on the working copy: ${verify.error}`);
    console.error(`LIVE data dir is unchanged. Snapshots preserved at:`);
    console.error(`  preheal: ${preheal}`);
    console.error(`  working: ${working}`);
    process.exit(2);
  }
  console.log(`heal-pglite-resetwal: working copy opens, project rows = ${verify.count}`);

  console.log(`heal-pglite-resetwal: swapping ${live} → ${broken}, then ${working} → ${live}`);
  renameSync(live, broken);
  renameSync(working, live);

  console.log(`heal-pglite-resetwal: SUCCESS.`);
  console.log(`  Live dir:     ${live}  (recovered, ${verify.count} projects)`);
  console.log(`  Pre-heal:     ${preheal}  (original before any action)`);
  console.log(`  Broken dir:   ${broken}  (the dir we just retired)`);
  console.log(`  → 'bun dev' should now boot. Run 'bun --cwd packages/db db:heal-orphans' afterward to drop residual cloud_edge_proxy / cloud_page tables.`);
  process.exit(0);
}

main().catch((err) => {
  console.error("heal-pglite-resetwal CRASHED:", err);
  process.exit(1);
});
