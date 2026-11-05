// Copyright (c) 2026 Netie AI. MIT.
//
// Fast test for the FreeRoute first-run bootstrap and the shipped scripts and
// CLI defaults around it. No build needed.
//
// - DATA_DIR/server.env holds JWT_SECRET, API_KEY_SECRET and
//   STORAGE_ENCRYPTION_KEY, so it is created 0600 and an older 0644 copy is
//   tightened on the next start.
// - A fresh install has no default dashboard password. With INITIAL_PASSWORD
//   unset the bootstrap invents nothing (onboarding sets it). A literal
//   CHANGEME is treated as unset and a warning is logged.
// - The CLI, scripts and help text default to port 3020, never 20128.
// - Shipped scripts print "FreeRoute", not the upstream name. Environment
//   variable names, file names and the ~/.omniroute data directory keep theirs.
//
// Run: node --import tsx --test netie/bootstrap.test.mjs
// (wired as `npm run test:bootstrap`, part of `npm test`.)

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const BOOTSTRAP = join(ROOT, "scripts", "build", "bootstrap-env.mjs");
const posix = process.platform !== "win32";
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

let sandbox;
let counter = 0;

before(() => {
  sandbox = mkdtempSync(join(tmpdir(), "freeroute-bootstrap-"));
});

after(() => {
  rmSync(sandbox, { recursive: true, force: true });
});

/**
 * Run bootstrapEnv() in a child process with a clean environment so the
 * developer's shell, ~/.omniroute and apps/router/.env cannot leak in.
 * Returns the merged env the function returns, plus stderr.
 */
function runBootstrap({ env = {}, files = {}, dataFiles = {} } = {}) {
  const home = join(sandbox, `home-${counter++}`);
  const cwd = join(home, "cwd");
  const dataDir = join(home, "data");
  mkdirSync(cwd, { recursive: true });
  mkdirSync(dataDir, { recursive: true });
  for (const [name, body] of Object.entries(files)) writeFileSync(join(cwd, name), body);
  for (const [name, spec] of Object.entries(dataFiles)) {
    const path = join(dataDir, name);
    writeFileSync(path, spec.body);
    if (spec.mode !== undefined) chmodSync(path, spec.mode);
  }

  const code =
    `import { bootstrapEnv } from ${JSON.stringify(pathToFileURL(BOOTSTRAP).href)};` +
    `process.stdout.write(JSON.stringify(bootstrapEnv()));`;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", code], {
    cwd,
    encoding: "utf8",
    timeout: 30_000,
    env: { PATH: process.env.PATH, HOME: home, USERPROFILE: home, DATA_DIR: dataDir, ...env },
  });
  assert.equal(result.status, 0, `bootstrap exited ${result.status}: ${result.stderr}`);
  return {
    merged: JSON.parse(result.stdout),
    stderr: result.stderr,
    dataDir,
    serverEnv: join(dataDir, "server.env"),
  };
}

const modeOf = (path) => statSync(path).mode & 0o777;

// ── item 39: server.env is owner-only ────────────────────────────────────────

test("a first run writes server.env with mode 0600 and the three secrets", { skip: !posix }, () => {
  const run = runBootstrap();
  assert.ok(existsSync(run.serverEnv), "server.env was written");
  assert.equal(modeOf(run.serverEnv), 0o600);
  const body = readFileSync(run.serverEnv, "utf8");
  for (const name of ["JWT_SECRET", "API_KEY_SECRET", "STORAGE_ENCRYPTION_KEY"]) {
    assert.match(body, new RegExp(`^${name}=[0-9a-f]{32,}$`, "m"), `${name} is persisted`);
  }
});

test("an existing 0644 server.env is tightened to 0600 even when nothing is regenerated", { skip: !posix }, () => {
  const complete = [
    "JWT_SECRET=" + "a".repeat(128),
    "API_KEY_SECRET=" + "b".repeat(64),
    "STORAGE_ENCRYPTION_KEY=" + "c".repeat(64),
    "STORAGE_ENCRYPTION_KEY_VERSION=v1",
    "",
  ].join("\n");
  const run = runBootstrap({ dataFiles: { "server.env": { body: complete, mode: 0o644 } } });
  assert.equal(modeOf(run.serverEnv), 0o600);
  assert.equal(readFileSync(run.serverEnv, "utf8"), complete, "contents were not rewritten");
  assert.equal(run.merged.JWT_SECRET, "a".repeat(128), "persisted secret still wins");
});

test("an existing 0644 server.env that needs a new secret is rewritten as 0600", { skip: !posix }, () => {
  const run = runBootstrap({
    dataFiles: { "server.env": { body: "JWT_SECRET=" + "d".repeat(128) + "\n", mode: 0o644 } },
  });
  assert.equal(modeOf(run.serverEnv), 0o600);
  const body = readFileSync(run.serverEnv, "utf8");
  assert.match(body, /^API_KEY_SECRET=[0-9a-f]{64}$/m);
  assert.match(body, new RegExp("^JWT_SECRET=" + "d".repeat(128) + "$", "m"));
});

test("a new data directory is created 0700", { skip: !posix }, () => {
  const home = join(sandbox, `home-${counter++}`);
  mkdirSync(home, { recursive: true });
  const dataDir = join(home, "fresh", "data");
  const code =
    `import { bootstrapEnv } from ${JSON.stringify(pathToFileURL(BOOTSTRAP).href)};` +
    `bootstrapEnv();`;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", code], {
    cwd: home,
    encoding: "utf8",
    timeout: 30_000,
    env: { PATH: process.env.PATH, HOME: home, DATA_DIR: dataDir },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(modeOf(dataDir), 0o700);
  assert.equal(modeOf(join(dataDir, "server.env")), 0o600);
});

// ── item 27: generated file and log lines say FreeRoute ─────────────────────

test("server.env and the bootstrap log say FreeRoute, not the upstream name", () => {
  const run = runBootstrap();
  const body = readFileSync(run.serverEnv, "utf8");
  assert.match(body.split("\n")[0], /^# Auto-generated by FreeRoute bootstrap/);
  assert.doesNotMatch(body, /OmniRoute/i);
  assert.doesNotMatch(run.stderr, /OmniRoute/);
});

// ── item 42: no well-known default password ─────────────────────────────────

test("with INITIAL_PASSWORD unset the bootstrap invents no password and never mentions CHANGEME", () => {
  const run = runBootstrap();
  assert.ok(!run.merged.INITIAL_PASSWORD, "no INITIAL_PASSWORD in the merged env");
  assert.ok(
    !readFileSync(run.serverEnv, "utf8").includes("INITIAL_PASSWORD"),
    "server.env does not persist a generated password"
  );
  assert.match(run.stderr, /INITIAL_PASSWORD is not set\. Onboarding will ask/);
  assert.doesNotMatch(run.stderr, /CHANGEME|using default/);
});

test("INITIAL_PASSWORD=CHANGEME from the shell is treated as unset, with a warning", () => {
  const run = runBootstrap({ env: { INITIAL_PASSWORD: "CHANGEME" } });
  assert.equal(run.merged.INITIAL_PASSWORD, "", "cleared to empty, not left as CHANGEME");
  assert.match(run.stderr, /well-known placeholder 'CHANGEME'\. Ignoring it/);
  assert.doesNotMatch(readFileSync(run.serverEnv, "utf8"), /CHANGEME/);
});

test("INITIAL_PASSWORD=CHANGEME copied from an old .env is treated as unset", () => {
  const run = runBootstrap({ files: { ".env": "INITIAL_PASSWORD=CHANGEME\nPORT=3020\n" } });
  assert.equal(run.merged.INITIAL_PASSWORD, "");
  assert.equal(run.merged.PORT, "3020", "the rest of that .env still applies");
  assert.match(run.stderr, /Ignoring it/);
});

test("a real INITIAL_PASSWORD is kept and not warned about", () => {
  const run = runBootstrap({ env: { INITIAL_PASSWORD: "correct-horse-battery" } });
  assert.equal(run.merged.INITIAL_PASSWORD, "correct-horse-battery");
  assert.doesNotMatch(run.stderr, /INITIAL_PASSWORD/);
});

test("the CLI setup command does not take CHANGEME from INITIAL_PASSWORD or --password", async () => {
  const { resolvePassword, isPlaceholderPassword } = await import(
    pathToFileURL(join(ROOT, "bin", "cli", "commands", "setup.mjs")).href
  );
  assert.equal(isPlaceholderPassword("CHANGEME"), true);
  assert.equal(isPlaceholderPassword(" CHANGEME "), true);
  assert.equal(isPlaceholderPassword("correct-horse-battery"), false);

  const saved = process.env.INITIAL_PASSWORD;
  try {
    process.env.INITIAL_PASSWORD = "CHANGEME";
    assert.equal(await resolvePassword({}, null, true, {}), "", "placeholder env reads as unset");
    process.env.INITIAL_PASSWORD = "correct-horse-battery";
    assert.equal(await resolvePassword({}, null, true, {}), "correct-horse-battery");
  } finally {
    if (saved === undefined) delete process.env.INITIAL_PASSWORD;
    else process.env.INITIAL_PASSWORD = saved;
  }
});

test("the server auth layer treats a CHANGEME INITIAL_PASSWORD as unset", async () => {
  process.env.DATA_DIR = join(sandbox, "auth-data");
  process.env.DISABLE_SQLITE_AUTO_BACKUP = "true";
  const { ensurePersistentManagementPasswordHash, hasManagementPasswordConfigured } = await import(
    "../src/lib/auth/managementPassword.ts"
  );

  const saved = process.env.INITIAL_PASSWORD;
  const warnings = [];
  const logger = { log() {}, warn: (m) => warnings.push(String(m)) };
  try {
    process.env.INITIAL_PASSWORD = "CHANGEME";
    assert.equal(hasManagementPasswordConfigured({}), false, "placeholder is not a configured password");

    const viaEnv = await ensurePersistentManagementPasswordHash({ settings: {}, logger });
    assert.equal(viaEnv.source, "missing");
    assert.equal(viaEnv.hash, null);
    assert.equal(process.env.INITIAL_PASSWORD, "", "later truthiness checks see it as unset");
    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /well-known placeholder "CHANGEME"\. Ignoring it/);

    const viaOption = await ensurePersistentManagementPasswordHash({
      settings: {},
      initialPassword: "CHANGEME",
      logger,
    });
    assert.equal(viaOption.source, "missing");
    assert.equal(viaOption.hash, null);

    process.env.INITIAL_PASSWORD = "correct-horse-battery";
    assert.equal(hasManagementPasswordConfigured({}), true, "a real password still counts");
  } finally {
    if (saved === undefined) delete process.env.INITIAL_PASSWORD;
    else process.env.INITIAL_PASSWORD = saved;
  }
});

// ── item 41: the CLI, scripts and help text default to 3020 ─────────────────

function walk(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue;
      walk(full, out);
    } else out.push(full);
  }
  return out;
}

const rel = (full) => relative(ROOT, full).split("\\").join("/");

// Files owned by other lanes in this round (browser login, tray icons).
const OTHER_LANES = new Set(["bin/cli/commands/login.mjs"]);
const inBinScope = (path) => !OTHER_LANES.has(path) && !path.startsWith("bin/cli/tray/");

const TEXT_FILE = /\.(mjs|js|jsx|ts|json|sh|md)$/;
const binFiles = () =>
  walk(join(ROOT, "bin")).filter((f) => TEXT_FILE.test(f) && inBinScope(rel(f)));

// Scripts that run on a user's machine or in a container, or print a default
// a user could copy. Maintainer tooling that targets a named remote host keeps
// that host's port and is not listed.
const RUNTIME_SCRIPTS = [
  "scripts/dev/healthcheck.mjs",
  "scripts/dev/codex-ws.sh",
  "scripts/dev/smoke-electron-packaged.mjs",
  "scripts/check/omniroute-verify.mjs",
  "scripts/ad-hoc/regen-opencode-config.ts",
  "scripts/build/runtime-env.mjs",
  "scripts/build/bootstrap-env.mjs",
];

test("no file under bin/ or the runtime scripts names the upstream port 20128", () => {
  const offenders = [];
  const files = [...binFiles().map(rel), ...RUNTIME_SCRIPTS];
  for (const file of files) {
    read(file)
      .split("\n")
      .forEach((line, i) => {
        if (/\b20128\b/.test(line)) offenders.push(`${file}:${i + 1}: ${line.trim().slice(0, 100)}`);
      });
  }
  assert.deepEqual(offenders, []);
});

test("every --port option and PORT fallback under bin/ defaults to 3020", () => {
  const bad = [];
  let optionCount = 0;
  for (const file of binFiles().filter((f) => /\.(mjs|jsx)$/.test(f))) {
    read(rel(file))
      .split("\n")
      .forEach((line, i) => {
        const opt = line.match(/\.option\(\s*"--port <port>".*,\s*"(\d+)"\s*\)/);
        if (opt) {
          optionCount++;
          if (opt[1] !== "3020") bad.push(`${rel(file)}:${i + 1}: --port default ${opt[1]}`);
        }
        const fallback = line.match(/process\.env\.PORT\s*(?:\?\?|\|\|)\s*"?(\d+)"?/);
        if (fallback && fallback[1] !== "3020") {
          bad.push(`${rel(file)}:${i + 1}: PORT fallback ${fallback[1]}`);
        }
      });
  }
  assert.ok(optionCount >= 10, `found ${optionCount} --port options`);
  assert.deepEqual(bad, []);
});

test("freeroute serve defaults to 3020, keeps the PORT and --port overrides, and matches the runtime", async () => {
  const serve = read("bin/cli/commands/serve.mjs");
  assert.match(serve, /parsePort\(opts\.port \?\? process\.env\.PORT \?\? "3020", 3020\)/);
  assert.ok(!/\b20128\b/.test(serve));

  const { resolveRuntimePorts } = await import(
    pathToFileURL(join(ROOT, "scripts", "build", "runtime-env.mjs")).href
  );
  assert.equal(resolveRuntimePorts({}).basePort, 3020, "npm start default");
  assert.equal(resolveRuntimePorts({ PORT: "4100" }).basePort, 4100, "PORT override");
});

test("the serve help text and every CLI locale say the default port is 3020", () => {
  const dir = join(ROOT, "bin", "cli", "locales");
  const files = readdirSync(dir).filter((f) => f.endsWith(".json"));
  assert.ok(files.length > 50, "locale files found");
  const missing = [];
  for (const file of files) {
    const json = JSON.parse(readFileSync(join(dir, file), "utf8"));
    if (!String(json.serve?.port ?? "").includes("3020")) missing.push(file);
  }
  assert.deepEqual(missing, []);
});

// ── item 27: shipped scripts and the CLI print FreeRoute ────────────────────

const PRINTS_UPSTREAM_NAME = /\bOmniRoute\b|\[omniroute\]/;
const COMMENT_LINE = /^\s*(\/\/|\/\*|\*|#)/;

// Packaged-executable and build-path names are identifiers, not output.
const IDENTIFIER_ONLY = new Set([
  "scripts/dev/smoke-electron-packaged.mjs",
  "scripts/build/prepare-electron-standalone.mjs",
]);

function shippedScriptFiles() {
  const list = new Set(RUNTIME_SCRIPTS);
  for (const dir of ["scripts/build", "scripts/dev", "scripts/docker"]) {
    for (const f of walk(join(ROOT, dir))) {
      if (/\.(mjs|ts|sh)$/.test(f)) list.add(rel(f));
    }
  }
  for (const f of [
    "scripts/postinstall.mjs",
    "scripts/check/check-supported-node-runtime.ts",
    "scripts/check-permissions.sh",
    "scripts/devin-bridge/select-live-model.mjs",
    "scripts/docs/gen-provider-reference.ts",
    "scripts/homolog/gen-promptfoo.mjs",
    "scripts/research/gen-budget-card-svg.mjs",
    "scripts/test/_vpsClient.mjs",
    "scripts/test/combo-live-vps.mjs",
  ]) {
    list.add(f);
  }
  return [...list].filter((f) => !IDENTIFIER_ONLY.has(f)).sort();
}

test("shipped scripts and the CLI do not print or write the upstream product name", () => {
  const files = [...shippedScriptFiles(), ...binFiles().map(rel).filter((f) => /\.(mjs|jsx|sh)$/.test(f))];
  const offenders = [];
  for (const file of files) {
    read(file)
      .split("\n")
      .forEach((line, i) => {
        if (COMMENT_LINE.test(line)) return;
        if (PRINTS_UPSTREAM_NAME.test(line)) {
          offenders.push(`${file}:${i + 1}: ${line.trim().slice(0, 110)}`);
        }
      });
  }
  assert.deepEqual(offenders, []);
});

test("the uninstaller removes the FreeRoute package and process, not an upstream install", () => {
  const src = read("scripts/build/uninstall.mjs");
  assert.match(src, /npm uninstall -g @netie\/freeroute/);
  assert.match(src, /pm2 delete freeroute/);
  assert.doesNotMatch(src, /npm uninstall -g omniroute/);
  assert.doesNotMatch(src, /pm2 delete omniroute/);
  const pkg = JSON.parse(read("package.json"));
  assert.equal(pkg.name, "@netie/freeroute");
});

test("freeroute serve prints a FreeRoute banner", () => {
  const serve = read("bin/cli/commands/serve.mjs");
  assert.ok(
    serve.includes("|  ___| _ __   ___    ___  |  _ \\\\   ___   _   _ | |_   ___"),
    "FreeRoute banner row present"
  );
  // The old banner's third row spelled the upstream name.
  assert.ok(!serve.includes("|_ __ ___  _ __ _| |__) |___"), "upstream banner gone");
});

test("the generated Codex config and the WebDAV realm say FreeRoute", () => {
  const codex = read("scripts/dev/codex-ws.sh");
  assert.match(codex, /name = "FreeRoute Local \(WS\)"/);
  assert.match(read("scripts/dev/webdav-handler.mjs"), /realm="FreeRoute WebDAV"/);
});
