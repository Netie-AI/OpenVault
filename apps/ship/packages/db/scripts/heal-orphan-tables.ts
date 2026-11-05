/**
 * One-shot heal: drop orphan tables left over from migrations that
 * were rewritten in-place (cloud_edge_proxy + cloud_page). The tables
 * never carried production data — they were ledger-pattern artifacts
 * removed when we switched to Oblien-namespace-as-source-of-truth.
 *
 * Safe to re-run: each DROP uses IF EXISTS. NEVER touches user data
 * (projects, deployments, services, organizations).
 *
 * Run:  bun --cwd packages/db scripts/heal-orphan-tables.ts
 */

import { db } from "../src/client";
import { sql } from "drizzle-orm";

const ORPHANS = ["cloud_edge_proxy", "cloud_page"] as const;

async function tableExists(name: string): Promise<boolean> {
  const rows = await db.execute(sql`
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = ${name}
    LIMIT 1
  `);
  return rows.rows.length > 0;
}

async function main() {
  const dropped: string[] = [];
  const skipped: string[] = [];

  for (const name of ORPHANS) {
    if (await tableExists(name)) {
      await db.execute(sql.raw(`DROP TABLE IF EXISTS "${name}" CASCADE`));
      dropped.push(name);
    } else {
      skipped.push(name);
    }
  }

  console.log("heal-orphan-tables: complete");
  if (dropped.length) console.log("  dropped:", dropped.join(", "));
  if (skipped.length) console.log("  not present (skipped):", skipped.join(", "));
  process.exit(0);
}

main().catch((err) => {
  console.error("heal-orphan-tables failed:", err);
  process.exit(1);
});
