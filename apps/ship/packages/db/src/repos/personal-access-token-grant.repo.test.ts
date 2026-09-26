import { describe, it, expect, beforeEach } from "vitest";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { PGlite } from "@electric-sql/pglite";
import { drizzle } from "drizzle-orm/pglite";
import { migrate } from "drizzle-orm/pglite/migrator";
import * as schema from "../schema";
import { createPersonalAccessTokenGrantRepo } from "./personal-access-token-grant.repo";

const MIGRATIONS_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../../drizzle");

/**
 * Real (in-memory PGlite) test for the per-token grant repo — the storage +
 * read contract behind scoped PATs. FK enforcement is disabled so we can seed
 * grant rows without a parent token row.
 */
async function freshRepo() {
  const client = new PGlite("memory://");
  const db = drizzle(client, { schema });
  await migrate(db, { migrationsFolder: MIGRATIONS_DIR });
  await client.exec("SET session_replication_role = replica;"); // skip FK seeding
  return createPersonalAccessTokenGrantRepo(db);
}

describe("personalAccessTokenGrant repo", () => {
  let repo: Awaited<ReturnType<typeof freshRepo>>;
  beforeEach(async () => {
    repo = await freshRepo();
  }, 30_000);

  it("createMany + listByToken round-trips grants for a token", async () => {
    await repo.createMany("tok_1", [
      { resourceType: "project", resourceId: "proj_a", permissions: ["read"] },
      { resourceType: "github_repository", resourceId: "acme/api", permissions: ["read", "write"] },
    ]);
    const list = await repo.listByToken("tok_1");
    expect(list).toHaveLength(2);
    expect(list.find((g) => g.resourceId === "proj_a")?.permissions).toEqual(["read"]);
    expect(list.find((g) => g.resourceId === "acme/api")?.permissions).toEqual(["read", "write"]);
  });

  it("isolates grants by token", async () => {
    await repo.createMany("tok_1", [{ resourceType: "project", resourceId: "proj_a", permissions: ["read"] }]);
    await repo.createMany("tok_2", [{ resourceType: "project", resourceId: "proj_b", permissions: ["admin"] }]);
    expect(await repo.listByToken("tok_1")).toHaveLength(1);
    expect((await repo.listByToken("tok_2"))[0]?.resourceId).toBe("proj_b");
  });

  it("findForResource returns a matching grant, null when absent", async () => {
    await repo.createMany("tok_1", [{ resourceType: "project", resourceId: "proj_a", permissions: ["write"] }]);
    expect((await repo.findForResource("tok_1", "project", "proj_a"))?.permissions).toEqual(["write"]);
    expect(await repo.findForResource("tok_1", "project", "proj_missing")).toBeNull();
    expect(await repo.findForResource("tok_1", "server", "proj_a")).toBeNull();
  });

  it("findForResource prefers a specific grant over the wildcard", async () => {
    await repo.createMany("tok_1", [
      { resourceType: "project", resourceId: "*", permissions: ["read"] },
      { resourceType: "project", resourceId: "proj_a", permissions: ["admin"] },
    ]);
    // Specific id → the specific grant.
    expect((await repo.findForResource("tok_1", "project", "proj_a"))?.permissions).toEqual(["admin"]);
    // Any other id → covered by the wildcard.
    expect((await repo.findForResource("tok_1", "project", "proj_z"))?.permissions).toEqual(["read"]);
  });

  it("deleteByToken clears a token's grants", async () => {
    await repo.createMany("tok_1", [{ resourceType: "project", resourceId: "proj_a", permissions: ["read"] }]);
    await repo.deleteByToken("tok_1");
    expect(await repo.listByToken("tok_1")).toHaveLength(0);
  });
});
