// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// Installs the mail app's own server and client dependencies with bun, when bun is
// installed. The root `npm ci` must work without bun: these dependencies are only
// needed to develop or test apps/email, and the mail image installs its own in its
// build stage (Dockerfile.webmail).
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
// npm-installed bun is a .cmd shim on Windows, which only a shell can start.
const shell = process.platform === "win32";

const probe = spawnSync("bun", ["--version"], { stdio: "ignore", shell });
if (probe.error || probe.status !== 0) {
  process.stdout.write(
    "[email] bun not found: skipping the mail app's own dependencies (server/, client/). " +
      "They are only needed to develop or test apps/email; the mail image installs its own.\n",
  );
  process.exit(0);
}

for (const dir of ["server", "client"]) {
  const run = spawnSync("bun", ["install"], { cwd: join(root, dir), stdio: "inherit", shell });
  if (run.status !== 0) process.exit(run.status ?? 1);
}
