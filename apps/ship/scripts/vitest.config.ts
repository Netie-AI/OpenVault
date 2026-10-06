// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// Runs scripts/*.test.ts under vitest (`npm run test:scripts`). Upstream ran
// them with `bun test`; this fork's toolchain is Node and npm only, so the
// "bun:test" import is aliased to vitest, which has the same describe, test
// and expect. Two Bun-only names the tests use are supplied here so the test
// files stay as upstream wrote them: `import.meta.dir` (the file's directory)
// and the `toStartWith` matcher (vitest-bun-compat.ts). The scripts/*.test.mjs
// files use node:test and run under `node --test` (see test:scripts in
// package.json).
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const scriptsDir = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: scriptsDir,
  define: { "import.meta.dir": JSON.stringify(scriptsDir.replace(/\/$/, "")) },
  resolve: {
    alias: { "bun:test": "vitest" },
  },
  test: {
    include: ["*.test.ts"],
    setupFiles: ["./vitest-bun-compat.ts"],
    // The CLI test runs scripts/*.ts as a child process with process.execPath,
    // which Bun runs natively. Node needs the tsx loader for that.
    env: { NODE_OPTIONS: `${process.env.NODE_OPTIONS ?? ""} --import tsx`.trim() },
  },
});
