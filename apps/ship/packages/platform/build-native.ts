// Modified by Netie AI, 2026: replaced Bun.build with esbuild (Node 22
// toolchain, no Bun runtime dependency). The mail-engine asset copy below
// tolerates a missing apps/email/engine defensively; apps/email is restored
// in this fork (mail hosting is in scope — see PRODUCT_ROLES.md), so that
// branch is not expected to run, but is kept rather than made unconditional
// again in case a stripped-down checkout ever omits it.
/** Build-time assembly of the shared engine for an owned Node worker. */
import { build as esbuild } from "esbuild";
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageDir = dirname(fileURLToPath(import.meta.url));
const root = resolve(packageDir, "../..");
const out = join(packageDir, "dist/native");
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
// esbuild, unlike Bun.build, never substitutes `process.env.*` on its own, so
// there is no "env: disable" escape hatch to reach for here — runtime
// configuration already belongs to the owning instance by default.
await esbuild({
  entryPoints: [join(packageDir, "src/native-worker.ts")],
  outfile: join(out, "engine-worker.mjs"),
  bundle: true,
  platform: "node",
  format: "esm",
  external: ["cpu-features", "ssh2", "dockerode"],
  // Bun.build gave every bundled CJS dependency a working `require` for free;
  // esbuild's ESM output does not, so a transitive dep that calls `require(...)`
  // dynamically (not statically analyzable, so esbuild can't rewrite it to an
  // import) throws "Dynamic require of ... is not supported" at runtime. The
  // standard esbuild fix: inject one at the top of the bundle.
  banner: {
    js: "import { createRequire as __netieCreateRequire } from 'node:module';\nconst require = __netieCreateRequire(import.meta.url);",
  },
});
const require = createRequire(join(root, "packages/db/package.json"));
const pglite = dirname(require.resolve("@electric-sql/pglite"));
mkdirSync(join(out, "pglite"));
for (const name of ["pglite.wasm", "pglite.data"]) cpSync(join(pglite, name), join(out, "pglite", name));
cpSync(join(root, "packages/db/drizzle"), join(out, "migrations"), { recursive: true });
const mailEngineDir = join(root, "apps/email/engine");
if (existsSync(mailEngineDir)) {
  cpSync(mailEngineDir, join(out, "engine"), { recursive: true });
} else {
  // `apps/email` (the iRedMail-based mail engine) was pruned from this fork.
  // Skip rather than crash: mail-server hosting is unavailable in the native
  // embedding target until that asset is restored or relocated.
  console.warn(
    "[platform] apps/email is not part of this fork; skipping mail engine asset copy. " +
      "Mail-server hosting features will be unavailable in dist/native/engine.",
  );
}
cpSync(join(root, "packages/adapters/src/infra/lua"), join(out, "lua"), { recursive: true });
cpSync(join(root, "apps/api/assets/geoip"), join(out, "assets/geoip"), { recursive: true });
console.log("[platform] built native Node worker and runtime assets");
