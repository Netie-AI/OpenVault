// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// postinstall: apply patches/oblien+2.4.0.patch to node_modules/oblien.
//
// Upstream applies this patch through its package manager's
// "patchedDependencies". npm has no such feature, so this script does it with
// the system `patch` tool. It is idempotent: an already-applied patch is
// detected with a reverse dry run and left alone, so `npm ci`, `npm install`
// and a rerun all end in the same state.
//
// A patch that no longer applies (oblien version drift) fails the install
// loudly. A machine with no `patch` binary (stock Windows) gets a clear warning
// and a working install without the patch: the oblien log stream then cannot
// be aborted early, which leaks a connection per closed log view.

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const TARGET = join(ROOT, "node_modules", "oblien");
const PATCH = join(ROOT, "patches", "oblien+2.4.0.patch");

function runPatch(args) {
  return spawnSync("patch", ["-p1", "-d", TARGET, "-i", PATCH, ...args], {
    encoding: "utf8",
    timeout: 30_000,
  });
}

if (!existsSync(TARGET)) {
  // Nothing to patch, e.g. an install that omitted the adapters workspace.
  console.log("[oblien-patch] node_modules/oblien not installed, nothing to patch");
  process.exit(0);
}

const reverse = runPatch(["-R", "-s", "-f", "--dry-run"]);
if (reverse.error?.code === "ENOENT") {
  console.warn(
    "[oblien-patch] WARNING: the `patch` command is not installed, so " +
      "patches/oblien+2.4.0.patch was NOT applied. Install it (Git for Windows " +
      "ships one in usr/bin) and rerun `npm run postinstall`.",
  );
  process.exit(0);
}
if (reverse.status === 0) {
  console.log("[oblien-patch] already applied");
  process.exit(0);
}

const apply = runPatch(["-N", "-s", "-f"]);
if (apply.status !== 0) {
  console.error(
    `[oblien-patch] FAILED to apply patches/oblien+2.4.0.patch (exit ${apply.status}):\n` +
      `${apply.stdout ?? ""}${apply.stderr ?? ""}`,
  );
  process.exit(1);
}
console.log("[oblien-patch] applied");
