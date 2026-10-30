import { describe, expect, test } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

// Guard: no migration may ADD a NOT NULL column without a DEFAULT.
//
// Two reasons this is unsafe:
//   1. It fails at migrate time on any table that already has rows.
//   2. It breaks cross-version dump/ingest (cloud transfer / project transfer):
//      an OLDER dump omits the column, Drizzle emits DEFAULT for it, and with no
//      default that's a NULL → NOT NULL violation on the newer receiver. The
//      companion filterRowToKnownColumns only fixes the NEWER-sender direction;
//      this rule closes the reverse. Additive columns must be nullable/defaulted.
//
// `SET NOT NULL` on an existing column is a different (deliberate) operation and
// is intentionally NOT matched here — only fresh ADD COLUMN.

const MIGRATIONS_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../drizzle");

function statements(sql: string): string[] {
  return sql
    .split(/-->\s*statement-breakpoint|;/i)
    .map((s) => s.trim())
    .filter(Boolean);
}

describe("migrations are additive-safe", () => {
  const files = readdirSync(MIGRATIONS_DIR).filter((f) => f.endsWith(".sql"));

  const offenders: string[] = [];
  for (const file of files) {
    const sql = readFileSync(resolve(MIGRATIONS_DIR, file), "utf8");
    for (const stmt of statements(sql)) {
      if (!/\bADD COLUMN\b/i.test(stmt)) continue;
      if (!/\bNOT NULL\b/i.test(stmt)) continue;
      if (/\bDEFAULT\b/i.test(stmt)) continue;
      offenders.push(`${file}: ${stmt.replace(/\s+/g, " ").slice(0, 140)}`);
    }
  }

  test("no ADD COLUMN ... NOT NULL without a DEFAULT", () => {
    expect(
      offenders,
      `A NOT NULL column added by a migration must carry a DEFAULT:\n${offenders.join("\n")}`,
    ).toEqual([]);
  });

  // Fail loud if the migrations path stops resolving — otherwise the check above
  // passes vacuously against zero files.
  test("actually scanned the migration set", () => {
    expect(files.length).toBeGreaterThan(10);
  });
});
