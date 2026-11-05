// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// Boots the real env.ts and the database client in a child process, outside a test runner,
// with a temporary HOME. With BETTER_AUTH_SECRET unset or set to the .env.example placeholder
// the app must generate a random per-install secret (mode 0600), seal new values under it,
// and still open values an earlier build sealed under the public placeholder.
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, statSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const apiDir = fileURLToPath(new URL("../..", import.meta.url));
const clientPath = fileURLToPath(new URL("../../../../packages/db/src/client.ts", import.meta.url));

let home: string;
beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), "freebuild-boot-"));
});
afterEach(() => {
  rmSync(home, { recursive: true, force: true });
});

function run(script: string, betterAuthSecret: string | undefined) {
  const env: NodeJS.ProcessEnv = { ...process.env, HOME: home, USERPROFILE: home, NODE_ENV: "production", VITEST: "", INTERNAL_TOKEN: "x".repeat(40) };
  delete env.OPENSHIP_DATA_DIR;
  delete env.PGLITE_DATA_DIR;
  delete env.OPENSHIP_AUTH_SECRET_FILE;
  delete env.DATABASE_URL;
  if (betterAuthSecret === undefined) delete env.BETTER_AUTH_SECRET;
  else env.BETTER_AUTH_SECRET = betterAuthSecret;
  const out = spawnSync(process.execPath, ["--import", "tsx", "--input-type=module", "-e", script], {
    cwd: apiDir,
    env,
    encoding: "utf8",
    timeout: 150_000,
    killSignal: "SIGKILL",
  });
  expect(out.status, out.stderr).toBe(0);
  return JSON.parse(out.stdout.trim().split("\n").at(-1)!) as Record<string, unknown>;
}

const clientScript = `
const { createEncryption, DEFAULT_ENCRYPTION_SECRET } = await import("@repo/db/encryption");
const { storageEncryption, closeDb } = await import(${JSON.stringify(clientPath)});
const { env } = await import("@repo/platform/engine/config/env");
const legacy = createEncryption(DEFAULT_ENCRYPTION_SECRET).encrypt("legacy-value");
const sealed = storageEncryption.encrypt("fresh-value");
let publicOpens = false;
try { createEncryption(DEFAULT_ENCRYPTION_SECRET).decrypt(sealed); publicOpens = true; } catch {}
console.log(JSON.stringify({
  envSecret: env.BETTER_AUTH_SECRET,
  legacyOpens: storageEncryption.decrypt(legacy),
  envKeyOpens: createEncryption(env.BETTER_AUTH_SECRET).decrypt(sealed),
  publicOpens,
}));
await closeDb();
process.exit(0);
`;

describe("a fresh install boots without the public placeholder key", () => {
  it("generates the secret once, shares it between env.ts and the client, and opens legacy values", () => {
    const secretFile = join(home, ".openship", "auth-secret");

    const first = run(clientScript, undefined);
    expect(existsSync(secretFile)).toBe(true);
    if (process.platform !== "win32") expect(statSync(secretFile).mode & 0o777).toBe(0o600);
    const generated = readFileSync(secretFile, "utf8").trim();
    expect(first).toMatchObject({ envSecret: generated, legacyOpens: "legacy-value", envKeyOpens: "fresh-value", publicOpens: false });
    expect(generated).toMatch(/^[0-9a-f]{64}$/);

    // The .env.example placeholder is treated as unset: same file, same secret, still no public key.
    const second = run(clientScript, "change-me-in-production");
    expect(second).toMatchObject({ envSecret: generated, legacyOpens: "legacy-value", publicOpens: false });
  }, 300_000);

  it("still opens values sealed under the old placeholder through the platform cipher (env vars, SSH credentials, clone tokens)", () => {
    // packages/platform/src/engine/lib/encryption.ts backs deployment env vars, SSH passwords and
    // passphrases, clone tokens, server GitHub tokens, webhook secrets and SMTP relay passwords.
    // An upgrade must not make those unreadable, and decryptEnvMap must not drop them.
    const script = `
      const { createEncryption, DEFAULT_ENCRYPTION_SECRET } = await import("@repo/db/encryption");
      const platform = await import("@repo/platform/engine/lib/encryption");
      const legacy = createEncryption(DEFAULT_ENCRYPTION_SECRET);
      const sealedLegacy = legacy.encrypt("legacy-value");
      const sealedFresh = platform.encrypt("fresh-value");
      let publicOpensFresh = false;
      try { legacy.decrypt(sealedFresh); publicOpensFresh = true; } catch {}
      console.log(JSON.stringify({
        legacyOpens: platform.decrypt(sealedLegacy),
        envMap: platform.decryptEnvMap({ API_TOKEN: sealedLegacy, NEW_TOKEN: sealedFresh }),
        freshOpens: platform.decrypt(sealedFresh),
        publicOpensFresh,
      }));
      process.exit(0);
    `;
    const result = run(script, undefined);
    expect(result).toMatchObject({
      legacyOpens: "legacy-value",
      envMap: { API_TOKEN: "legacy-value", NEW_TOKEN: "fresh-value" },
      freshOpens: "fresh-value",
      publicOpensFresh: false,
    });
  }, 300_000);

  it("lets an operator-set BETTER_AUTH_SECRET win and writes no file", () => {
    const script = `
      const { env } = await import("@repo/platform/engine/config/env");
      console.log(JSON.stringify({ envSecret: env.BETTER_AUTH_SECRET }));
    `;
    const out = run(script, "operator-chosen-secret-0123456789abcdef");
    expect(out.envSecret).toBe("operator-chosen-secret-0123456789abcdef");
    expect(existsSync(join(home, ".openship", "auth-secret"))).toBe(false);
  }, 150_000);
});
