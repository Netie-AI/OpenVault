// Copyright (c) 2026 Netie AI. MIT.
//
// Live end-to-end test: built FreeRoute against a REAL OpenVault.
//
// Needs: `uv` on PATH, the OpenVault repo (its OpenMW/ directory), and a
// FreeRoute production build (`npm run build` or `npm run build:backend`).
// The OpenMW directory defaults to ../../OpenMW from this package (the
// layout of the OpenVault monorepo); override with OPENVAULT_OPENMW_DIR.
//
// What it does, in order:
//   1. Starts real OpenVault (`uv run openmw console`) with a temp OPENVAULT_HOME.
//      OpenVault creates its master key and admin_token there on first start.
//   2. Starts a fake OpenAI-compatible upstream on loopback that records the
//      Authorization header it receives.
//   3. Stores a provider key in OpenVault over its HTTP API (POST /api/keys,
//      with X-OpenVault-Admin read from $OPENVAULT_HOME/admin_token).
//   4. Starts built FreeRoute with a temp DATA_DIR, pointed at that OpenVault.
//   5. Creates an OpenAI-compatible node + a keyless connection to the fake
//      upstream through FreeRoute's management API.
//   6. Mints a client token in OpenVault and sends a chat completion through
//      FreeRoute with it, then checks the upstream saw the vault secret and
//      OpenVault audited a secret_reveal.
//   7. Negative cases: bogus client token, disabled providers, a key save
//      into FreeRoute (POST and PUT /api/providers, bulk, import, the cloud
//      agent credentials route, the `keys add` CLI command), the /authorize
//      OAuth callback, the Dario sidecar status route, a tenant-custody key
//      that must never be spent, a connect to a non-loopback address, OpenVault
//      answering 429 on verify, a SQLite backup carrying known secrets that is
//      imported and must leave none of them in the live database file, a wrong
//      admin token, OpenVault stopped.
//   8. Greps FreeRoute's DATA_DIR and logs for every secret the test planted.
// Every process and temp dir is removed on exit, pass or fail.
//
// The `keys add` step runs the real CLI entry point (bin/omniroute.mjs). That
// entry point refuses to start on a Node below its security floor (22.22.2), so
// on an older Node the step drives the same Commander program through the entry
// point's loader instead and logs which path ran. Set FREEROUTE_E2E_CLI_NODE to
// a supported node binary to force the real entry point.
//
// Run: npm run test:e2e

import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { connect as netConnect } from "node:net";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
  createWriteStream,
} from "node:fs";
import { networkInterfaces, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { randomBytes } from "node:crypto";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";

const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const OPENMW_DIR = resolve(process.env.OPENVAULT_OPENMW_DIR || join(ROOT, "..", "..", "OpenMW"));

const PROVIDER_SECRET = `sk-e2e-vault-secret-${randomBytes(12).toString("hex")}`;
// A tenant-custody key: stored in OpenVault, never spent by a router.
const TENANT_SECRET = `sk-e2e-tenant-secret-${randomBytes(12).toString("hex")}`;
const BULK_SECRET = `sk-e2e-bulk-secret-${randomBytes(12).toString("hex")}`;
// Keys a caller tries to hand FreeRoute through the cloud agent route and the CLI.
const AGENT_SECRET = `sk-e2e-agent-secret-${randomBytes(12).toString("hex")}`;
const CLI_SECRET = `sk-e2e-cli-secret-${randomBytes(12).toString("hex")}`;
// Planted in a SQLite backup that is then imported (one per place a key can hide).
const IMPORT_SECRETS = {
  apiKey: `sk-e2e-import-apikey-${randomBytes(12).toString("hex")}`,
  extraKey: `sk-e2e-import-extra-${randomBytes(12).toString("hex")}`,
  agentKey: `sk-e2e-import-agent-${randomBytes(12).toString("hex")}`,
  clientKey: `sk-e2e-import-client-${randomBytes(12).toString("hex")}`,
  embeddingKey: `sk-e2e-import-embed-${randomBytes(12).toString("hex")}`,
};
const MANAGEMENT_API_KEY = `sk-e2e-mgmt-${randomBytes(16).toString("hex")}`;
const BOGUS_CLIENT_TOKEN = `ov_bogus_${randomBytes(16).toString("hex")}`;
const CANNED_CONTENT = `canned-e2e-reply-${randomBytes(4).toString("hex")}`;
const UPSTREAM_MODEL = "gpt-e2e";
const NODE_PREFIX = "e2e";
const TENANT_NODE_PREFIX = "e2etenant";

const TMP = mkdtempSync(join(tmpdir(), "freeroute-e2e-"));
const OPENVAULT_HOME = join(TMP, "openvault-home");
const LOG_DIR = join(TMP, "logs");
mkdirSync(OPENVAULT_HOME, { recursive: true, mode: 0o700 });
mkdirSync(LOG_DIR, { recursive: true });

const children = new Set();
const servers = new Set();
const results = [];

function log(message) {
  process.stdout.write(`[e2e] ${message}\n`);
}

async function step(name, fn) {
  const started = Date.now();
  try {
    const value = await fn();
    results.push({ name, ok: true });
    log(`PASS ${name} (${Date.now() - started}ms)`);
    return value;
  } catch (error) {
    results.push({ name, ok: false });
    log(`FAIL ${name}: ${error?.stack || error}`);
    throw error;
  }
}

function getFreePort() {
  return new Promise((resolvePort, reject) => {
    const srv = createServer();
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolvePort(port));
    });
    srv.on("error", reject);
  });
}

/** Spawn in its own process group so the whole tree (uv -> python, node -> next) can be killed. */
function startProcess(name, command, args, options) {
  const logPath = join(LOG_DIR, `${name}.log`);
  const out = createWriteStream(logPath, { flags: "a" });
  const child = spawn(command, args, {
    ...options,
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.pipe(out);
  child.stderr.pipe(out);
  child.logPath = logPath;
  child.label = name;
  child.exited = new Promise((resolveExit) => child.on("exit", resolveExit));
  children.add(child);
  return child;
}

async function stopProcess(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) {
    children.delete(child);
    return;
  }
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    /* already gone */
  }
  const exited = await Promise.race([
    child.exited.then(() => true),
    sleep(10_000).then(() => false),
  ]);
  if (!exited) {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {
      /* already gone */
    }
    await Promise.race([child.exited, sleep(5_000)]);
  }
  children.delete(child);
}

function tail(path, lines = 60) {
  try {
    return readFileSync(path, "utf8").split("\n").slice(-lines).join("\n");
  } catch {
    return "(no log)";
  }
}

async function waitForStatus(url, child, timeoutMs, expected = 200) {
  const deadline = Date.now() + timeoutMs;
  let last = "no response";
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(
        `${child.label} exited (${child.exitCode}) before ${url} came up\n${tail(child.logPath)}`
      );
    }
    try {
      const res = await fetch(url);
      if (res.status === expected) return;
      last = `status ${res.status}`;
    } catch (error) {
      last = error.message;
    }
    await sleep(500);
  }
  throw new Error(`${url} not ready after ${timeoutMs}ms (${last})\n${tail(child.logPath)}`);
}

async function readJson(res) {
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { __raw: text };
  }
}

// ── fake upstream ────────────────────────────────────────────────────────────

const upstreamCalls = [];
// Every Authorization header FreeRoute sent the fake upstream. Never cleared.
const allUpstreamAuth = [];
// The same, with the request that carried it (for a failure message).
const allUpstreamRequests = [];
// OpenVault's own chat probe (OpenMW vault/chat_probe.py): 30 to 120 s after
// OpenVault starts it POSTs "Reply with OK" to the base_url of every enabled
// OpenAI-compatible key with that key, tenant custody included, from httpx. It
// is OpenVault talking to its own key's endpoint, not FreeRoute, so it is
// recorded apart. Counting it as FreeRoute traffic made the tenant check flaky
// (it passed or failed on the random jitter). Both signs must match, so no
// FreeRoute request can be taken for a probe.
const openVaultProbes = [];
function isOpenVaultChatProbe(req, body) {
  return /^python-httpx\//i.test(req.headers["user-agent"] || "") && /Reply with OK/.test(body);
}

function startFakeUpstream() {
  return new Promise((resolveServer, reject) => {
    const srv = createServer((req, res) => {
      let body = "";
      req.on("data", (chunk) => (body += chunk));
      req.on("end", () => {
        const path = new URL(req.url, "http://127.0.0.1").pathname;
        const record = {
          at: new Date().toISOString(),
          method: req.method,
          path,
          authorization: req.headers.authorization || "",
        };
        if (isOpenVaultChatProbe(req, body)) {
          openVaultProbes.push(record);
        } else {
          upstreamCalls.push({ method: req.method, path, authorization: req.headers.authorization });
          allUpstreamAuth.push(record.authorization);
          allUpstreamRequests.push(record);
        }
        res.setHeader("content-type", "application/json");
        // Like a real provider: every route needs the key. An upstream that
        // answered without one would hide a router that never sends it.
        if (req.headers.authorization !== `Bearer ${PROVIDER_SECRET}`) {
          res.writeHead(401);
          res.end(
            JSON.stringify({ error: { message: "invalid api key", type: "invalid_request_error" } })
          );
          return;
        }
        if (req.method === "GET" && /\/models$/.test(path)) {
          res.writeHead(200);
          res.end(
            JSON.stringify({
              object: "list",
              data: [{ id: UPSTREAM_MODEL, object: "model", owned_by: "e2e" }],
            })
          );
          return;
        }
        if (req.method === "POST" && /\/chat\/completions$/.test(path)) {
          let parsed = {};
          try {
            parsed = JSON.parse(body || "{}");
          } catch {
            /* keep empty */
          }
          const created = Math.floor(Date.now() / 1000);
          if (parsed.stream) {
            res.writeHead(200, { "content-type": "text/event-stream" });
            const chunk = (delta, finish = null) =>
              `data: ${JSON.stringify({
                id: "chatcmpl-e2e",
                object: "chat.completion.chunk",
                created,
                model: UPSTREAM_MODEL,
                choices: [{ index: 0, delta, finish_reason: finish }],
              })}\n\n`;
            res.write(chunk({ role: "assistant", content: CANNED_CONTENT }));
            res.write(chunk({}, "stop"));
            res.end("data: [DONE]\n\n");
            return;
          }
          res.writeHead(200);
          res.end(
            JSON.stringify({
              id: "chatcmpl-e2e",
              object: "chat.completion",
              created,
              model: UPSTREAM_MODEL,
              choices: [
                {
                  index: 0,
                  message: { role: "assistant", content: CANNED_CONTENT },
                  finish_reason: "stop",
                },
              ],
              usage: { prompt_tokens: 3, completion_tokens: 3, total_tokens: 6 },
            })
          );
          return;
        }
        res.writeHead(404);
        res.end(
          JSON.stringify({ error: { message: `fake upstream has no ${req.method} ${path}` } })
        );
      });
    });
    srv.listen(0, "127.0.0.1", () => resolveServer(srv));
    srv.on("error", reject);
    servers.add(srv);
  });
}

// ── OpenVault ────────────────────────────────────────────────────────────────

let openVault;
let openVaultUrl;

async function startOpenVault() {
  if (!existsSync(join(OPENMW_DIR, "pyproject.toml"))) {
    throw new Error(
      `OpenVault OpenMW directory not found at ${OPENMW_DIR} (set OPENVAULT_OPENMW_DIR)`
    );
  }
  const port = await getFreePort();
  openVaultUrl = `http://127.0.0.1:${port}`;
  openVault = startProcess(
    "openvault",
    "uv",
    [
      "run",
      "--directory",
      OPENMW_DIR,
      "openmw",
      "console",
      "--port",
      String(port),
      "--no-open-browser",
      "--mock-health",
      "--precheck-interval",
      "3600",
    ],
    { cwd: OPENMW_DIR, env: { ...process.env, OPENVAULT_HOME } }
  );
  await waitForStatus(`${openVaultUrl}/api/healthz`, openVault, 180_000);
}

function adminToken() {
  return readFileSync(join(OPENVAULT_HOME, "admin_token"), "utf8").trim();
}

async function openVaultCall(method, path, body) {
  const res = await fetch(`${openVaultUrl}${path}`, {
    method,
    headers: { "content-type": "application/json", "X-OpenVault-Admin": adminToken() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: res.status, body: await readJson(res) };
}

function readAudit() {
  const path = join(OPENVAULT_HOME, "secret_audit.jsonl");
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

// ── FreeRoute ────────────────────────────────────────────────────────────────

const DATA_DIR = join(TMP, "freeroute-data");
mkdirSync(DATA_DIR, { recursive: true });

async function startFreeRoute(name, { adminTokenPath }) {
  const port = await getFreePort();
  // No HOST or OMNIROUTE_HOSTNAME: the bind under test is FreeRoute's default.
  const inherited = { ...process.env };
  delete inherited.HOST;
  delete inherited.OMNIROUTE_HOSTNAME;
  const child = startProcess(name, process.execPath, ["scripts/dev/run-next.mjs", "start"], {
    cwd: ROOT,
    env: {
      ...inherited,
      PORT: String(port),
      DASHBOARD_PORT: String(port),
      API_PORT: String(port),
      DATA_DIR,
      OPENVAULT_URL: openVaultUrl,
      OPENVAULT_ADMIN_TOKEN_PATH: adminTokenPath,
      NODE_ENV: "production",
      DISABLE_SQLITE_AUTO_BACKUP: "true",
      OMNIROUTE_DISABLE_BACKGROUND_SERVICES: "true",
      OMNIROUTE_DISABLE_LOCAL_HEALTHCHECK: "true",
      OMNIROUTE_DISABLE_TOKEN_HEALTHCHECK: "true",
      OMNIROUTE_API_KEY: MANAGEMENT_API_KEY,
      REQUIRE_API_KEY: "true",
    },
  });
  const url = `http://127.0.0.1:${port}`;
  await waitForStatus(`${url}/healthz`, child, 120_000);
  return { child, url, port };
}

const mgmt = (extra = {}) => ({
  authorization: `Bearer ${MANAGEMENT_API_KEY}`,
  "content-type": "application/json",
  ...extra,
});

async function chat(baseUrl, bearer, model, extra = {}) {
  const res = await fetch(`${baseUrl}/v1/chat/completions`, {
    method: "POST",
    headers: { authorization: `Bearer ${bearer}`, "content-type": "application/json" },
    body: JSON.stringify({
      model,
      stream: false,
      messages: [{ role: "user", content: "say the canned thing" }],
      ...extra,
    }),
  });
  return { status: res.status, body: await readJson(res) };
}

// ── bind probe ───────────────────────────────────────────────────────────────

/** First non-internal IPv4 address of this machine (what `hostname -I` lists first), or null. */
function nonLoopbackIPv4() {
  for (const addrs of Object.values(networkInterfaces())) {
    for (const a of addrs || []) {
      if (a.family === "IPv4" && !a.internal && !a.address.startsWith("127.")) return a.address;
    }
  }
  return null;
}

/** Resolves "connected", or the socket error code (e.g. "ECONNREFUSED"). */
function tcpProbe(host, port) {
  return new Promise((resolveProbe) => {
    const sock = netConnect({ host, port });
    const done = (outcome) => {
      sock.destroy();
      resolveProbe(outcome);
    };
    sock.setTimeout(5_000, () => done("ETIMEDOUT"));
    sock.once("connect", () => done("connected"));
    sock.once("error", (error) => done(error.code || String(error)));
  });
}

// ── CLI ──────────────────────────────────────────────────────────────────────

// The CLI entry point refuses to start on a Node below its security floor
// (bin/nodeRuntimeSupport.mjs), before it parses any command. On such a host the
// same Commander program is driven through the entry point's own loader sequence
// (tsx, polyfill, alias resolver, createProgram, parseAsync), minus only that
// version gate, and the run is labelled so the report says which path ran.
const CLI_LOADER_SCRIPT = `
import { join } from "node:path";
import { pathToFileURL } from "node:url";
const root = process.env.E2E_CLI_ROOT;
await import("tsx/esm");
await import(pathToFileURL(join(root, "open-sse", "utils", "setupPolyfill.ts")).href);
const { registerAliasResolver } = await import(pathToFileURL(join(root, "bin", "aliasResolver.mjs")).href);
await registerAliasResolver(root);
try {
  const { createProgram } = await import(pathToFileURL(join(root, "bin", "cli", "program.mjs")).href);
  await createProgram().parseAsync(JSON.parse(process.env.E2E_CLI_ARGV));
} catch (err) {
  if (err.exitCode !== undefined) process.exit(err.exitCode);
  console.error(err.message);
  process.exit(1);
}
`;

/** Run the FreeRoute CLI against `dataDir`. Returns { status, stdout, stderr, path }. */
async function runCli(args, { dataDir, input } = {}) {
  const { getNodeRuntimeSupport } = await import(
    pathToFileURL(join(ROOT, "bin", "nodeRuntimeSupport.mjs")).href
  );
  // FREEROUTE_E2E_CLI_NODE: a supported Node binary to run the real entry point
  // with, when the Node running this script is below the CLI's floor.
  const cliNode = process.env.FREEROUTE_E2E_CLI_NODE || "";
  const support = cliNode ? { nodeCompatible: true } : getNodeRuntimeSupport();
  const env = {
    ...process.env,
    DATA_DIR: dataDir,
    OMNIROUTE_CLI_SKIP_REPO_ENV: "1",
    OMNIROUTE_NO_UPDATE_NOTIFIER: "1",
  };
  const run = support.nodeCompatible
    ? {
        argv: [join(ROOT, "bin", "omniroute.mjs"), ...args],
        env,
        path: cliNode ? `bin/omniroute.mjs on ${cliNode}` : "bin/omniroute.mjs",
      }
    : {
        argv: ["--input-type=module", "-e", CLI_LOADER_SCRIPT],
        env: { ...env, E2E_CLI_ROOT: ROOT, E2E_CLI_ARGV: JSON.stringify(["node", "omniroute", ...args]) },
        path: `program.mjs via the entry point's loader (entry point gate: Node ${support.nodeVersion} is below the floor)`,
      };
  const out = spawnSync(cliNode || process.execPath, run.argv, {
    cwd: ROOT,
    env: run.env,
    input,
    encoding: "utf8",
    timeout: 120_000,
  });
  return { status: out.status, stdout: out.stdout || "", stderr: out.stderr || "", path: run.path };
}

// ── secret grep ──────────────────────────────────────────────────────────────

function filesUnder(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...filesUnder(full));
    else if (st.isFile()) out.push(full);
  }
  return out;
}

function filesContaining(paths, needle) {
  const bytes = Buffer.from(needle, "utf8");
  return paths.filter((path) => readFileSync(path).includes(bytes));
}

// ── main ─────────────────────────────────────────────────────────────────────

async function cleanup() {
  for (const child of [...children]) await stopProcess(child);
  for (const srv of servers) await new Promise((r) => srv.close(r));
  servers.clear();
  rmSync(TMP, { recursive: true, force: true });
}

async function main() {
  log(`temp root ${TMP}`);
  log(`OpenMW dir ${OPENMW_DIR}`);

  const upstream = await step("fake upstream starts", startFakeUpstream);
  const upstreamUrl = `http://127.0.0.1:${upstream.address().port}/v1`;
  // A second endpoint on the same fake upstream that only a tenant key names.
  const tenantUpstreamUrl = `http://127.0.0.1:${upstream.address().port}/tenant/v1`;

  await step("real OpenVault starts and answers /api/healthz", startOpenVault);

  await step("OpenVault created a mode-0600 admin_token", async () => {
    const st = statSync(join(OPENVAULT_HOME, "admin_token"));
    assert.equal(st.mode & 0o777, 0o600, `admin_token mode is ${(st.mode & 0o777).toString(8)}`);
    assert.ok(adminToken().length > 0);
  });

  await step("OpenVault refuses /api/keys without X-OpenVault-Admin (401)", async () => {
    const res = await fetch(`${openVaultUrl}/api/keys`);
    assert.equal(res.status, 401);
    const body = await readJson(res);
    assert.equal(body.error?.type, "openvault_unauthenticated");
  });

  const vaultKey = await step("provider key stored in OpenVault via POST /api/keys", async () => {
    const { status, body } = await openVaultCall("POST", "/api/keys", {
      label: "freeroute-e2e",
      provider: "openai",
      secret: PROVIDER_SECRET,
      role: "primary",
      base_url: upstreamUrl,
      priority: 100,
    });
    assert.equal(status, 200, JSON.stringify(body));
    assert.ok(body.id, "key id");
    assert.ok(
      !JSON.stringify(body).includes(PROVIDER_SECRET),
      "create response must not echo the secret"
    );
    return body;
  });

  // Tenant-custody keys go in before FreeRoute starts, so its first key list
  // (cached 60s) already holds them. One shares the pooled key's endpoint and
  // outranks it on priority; the other is the only key for its endpoint.
  const tenantKeys = await step(
    "tenant-custody keys stored in OpenVault (custody: tenant)",
    async () => {
      const created = [];
      for (const [label, baseUrl, provider] of [
        ["freeroute-e2e-tenant-same-endpoint", upstreamUrl, "openai"],
        ["freeroute-e2e-tenant-only-endpoint", tenantUpstreamUrl, "custom"],
      ]) {
        const { status, body } = await openVaultCall("POST", "/api/keys", {
          label,
          provider,
          secret: TENANT_SECRET,
          role: "primary",
          base_url: baseUrl,
          priority: 1000,
          custody: "tenant",
        });
        assert.equal(status, 200, JSON.stringify(body));
        assert.equal(body.custody, "tenant", JSON.stringify(body));
        created.push(body);
      }
      const listed = await openVaultCall("GET", "/api/keys");
      for (const key of created) {
        const row = listed.body.keys?.find((k) => k.id === key.id);
        assert.equal(row?.custody, "tenant", `GET /api/keys row ${JSON.stringify(row)}`);
      }
      return created;
    }
  );

  // Baseline before FreeRoute exists, so before it can ask OpenVault for the
  // secret. Creating a connection starts a background model sync that reads
  // the key, which can land before the connection test; the 60s secret cache
  // then serves the test and the chat without a second reveal.
  const auditBefore = readAudit().filter((e) => e.event === "secret_reveal").length;

  const adminTokenPath = join(OPENVAULT_HOME, "admin_token");
  let fr = await step("built FreeRoute starts against OpenVault", () =>
    startFreeRoute("freeroute-1", { adminTokenPath })
  );

  await step(
    "POST /api/providers with an apiKey is refused (501 keys_managed_by_openvault)",
    async () => {
      const res = await fetch(`${fr.url}/api/providers`, {
        method: "POST",
        headers: mgmt(),
        body: JSON.stringify({
          provider: "openai",
          name: "must-not-store",
          apiKey: PROVIDER_SECRET,
        }),
      });
      const body = await readJson(res);
      assert.equal(res.status, 501, JSON.stringify(body));
      assert.equal(body.error?.code, "keys_managed_by_openvault");
    }
  );

  const node = await step("OpenAI-compatible node created for the fake upstream", async () => {
    const res = await fetch(`${fr.url}/api/provider-nodes`, {
      method: "POST",
      headers: mgmt(),
      body: JSON.stringify({
        type: "openai-compatible",
        name: "E2E upstream",
        prefix: NODE_PREFIX,
        apiType: "chat",
        baseUrl: upstreamUrl,
      }),
    });
    const body = await readJson(res);
    assert.equal(res.status, 201, JSON.stringify(body));
    assert.ok(body.node?.id);
    return body.node;
  });

  const connection = await step(
    "keyless connection created on that node (no apiKey field)",
    async () => {
      const res = await fetch(`${fr.url}/api/providers`, {
        method: "POST",
        headers: mgmt(),
        body: JSON.stringify({ provider: node.id, name: "e2e-keyless" }),
      });
      const body = await readJson(res);
      assert.ok(res.status === 200 || res.status === 201, `${res.status} ${JSON.stringify(body)}`);
      const conn = body.connection ?? body;
      assert.ok(conn.id, JSON.stringify(body));
      assert.ok(!conn.apiKey, "connection must not carry an apiKey");
      return conn;
    }
  );
  log(`node ${node.id}, connection ${connection.id}`);

  const connectionCount = async (providerId) => {
    const list = await readJson(
      await fetch(`${fr.url}/api/providers?provider=${encodeURIComponent(providerId)}`, {
        headers: mgmt(),
      })
    );
    assert.ok(Array.isArray(list.connections), JSON.stringify(list));
    return list.connections.filter((c) => c.provider === providerId).length;
  };

  await step(
    "PUT /api/providers/{id} with an apiKey is refused (501 keys_managed_by_openvault)",
    async () => {
      const res = await fetch(`${fr.url}/api/providers/${connection.id}`, {
        method: "PUT",
        headers: mgmt(),
        body: JSON.stringify({ apiKey: BULK_SECRET }),
      });
      const body = await readJson(res);
      assert.equal(res.status, 501, JSON.stringify(body));
      assert.equal(body.error?.code, "keys_managed_by_openvault", JSON.stringify(body));
      const after = await readJson(
        await fetch(`${fr.url}/api/providers/${connection.id}`, { headers: mgmt() })
      );
      const row = after.connection ?? after;
      assert.ok(!row.apiKey, `connection gained an apiKey: ${JSON.stringify(row)}`);
    }
  );

  await step(
    "POST /api/providers/bulk with an apiKey is refused (501 keys_managed_by_openvault)",
    async () => {
      const before = await connectionCount(node.id);
      const res = await fetch(`${fr.url}/api/providers/bulk`, {
        method: "POST",
        headers: mgmt(),
        body: JSON.stringify({
          provider: node.id,
          entries: [{ name: "bulk-must-not-store", apiKey: BULK_SECRET }],
        }),
      });
      const body = await readJson(res);
      assert.equal(res.status, 501, JSON.stringify(body));
      assert.equal(body.error?.code, "keys_managed_by_openvault", JSON.stringify(body));
      assert.equal(await connectionCount(node.id), before, "bulk created a connection");
    }
  );

  await step(
    "POST /api/providers/import with an apiKey is refused (501 keys_managed_by_openvault)",
    async () => {
      const before = await connectionCount(node.id);
      const res = await fetch(`${fr.url}/api/providers/import`, {
        method: "POST",
        headers: mgmt(),
        body: JSON.stringify({
          entries: [{ provider: node.id, name: "import-must-not-store", apiKey: BULK_SECRET }],
        }),
      });
      const body = await readJson(res);
      assert.equal(res.status, 501, JSON.stringify(body));
      assert.equal(body.error?.code, "keys_managed_by_openvault", JSON.stringify(body));
      assert.equal(await connectionCount(node.id), before, "import created a connection");
    }
  );

  await step(
    "POST /api/v1/agents/credentials is refused (501 keys_managed_by_openvault) and stores nothing",
    async () => {
      const res = await fetch(`${fr.url}/api/v1/agents/credentials`, {
        method: "POST",
        headers: mgmt(),
        body: JSON.stringify({ provider: "devin", apiKey: AGENT_SECRET }),
      });
      const body = await readJson(res);
      assert.equal(res.status, 501, JSON.stringify(body));
      assert.equal(body.error?.code, "keys_managed_by_openvault", JSON.stringify(body));
      assert.ok(!JSON.stringify(body).includes(AGENT_SECRET), "the refusal echoed the key");
      const listed = await fetch(`${fr.url}/api/v1/agents/credentials`, { headers: mgmt() });
      const listBody = await readJson(listed);
      assert.equal(listed.status, 200, JSON.stringify(listBody));
      assert.deepEqual(listBody.data, [], `a credential was stored: ${JSON.stringify(listBody)}`);
    }
  );

  await step(
    "GET /api/services/dario/status gets 501 consumer_subscription_pooling_disabled (with and without the management bearer)",
    async () => {
      for (const headers of [{}, mgmt()]) {
        const res = await fetch(`${fr.url}/api/services/dario/status`, { headers });
        const body = await readJson(res);
        assert.equal(res.status, 501, JSON.stringify(body));
        assert.equal(body.error?.code, "consumer_subscription_pooling_disabled", JSON.stringify(body));
        assert.equal(typeof body.error?.message, "string");
      }
    }
  );

  await step(
    "CLI `keys add` refuses to write a key locally, exits non-zero, and does not echo it",
    async () => {
      for (const [label, args, input] of [
        ["positional key", ["keys", "add", "openai", CLI_SECRET], undefined],
        ["--stdin key", ["keys", "add", "openai", "--stdin"], `${CLI_SECRET}\n`],
      ]) {
        const out = await runCli(args, { dataDir: DATA_DIR, input });
        const text = `${out.stdout}\n${out.stderr}`;
        log(`CLI ${label} via ${out.path}: exit ${out.status}`);
        assert.notEqual(out.status, 0, `${label} exited 0:\n${text}`);
        assert.equal(out.status, 1, `${label} exit code ${out.status}:\n${text}`);
        // A refusal for the right reason, not a crash or the Node version gate.
        assert.match(text, /keys_managed_by_openvault/, text);
        assert.match(text, /http:\/\/127\.0\.0\.1:3010\/keys/, text);
        assert.doesNotMatch(text, /is not supported/, text);
        assert.ok(!text.includes(CLI_SECRET), `${label}: the refusal echoed the key`);
      }
      // Pointed at the live data directory: no provider connection gained a key.
      const list = await readJson(
        await fetch(`${fr.url}/api/providers?provider=openai`, { headers: mgmt() })
      );
      assert.ok(Array.isArray(list.connections), JSON.stringify(list));
      assert.deepEqual(
        list.connections.filter((c) => c.apiKey),
        [],
        "a connection holds an apiKey after `keys add`"
      );
      assert.deepEqual(
        filesContaining(filesUnder(DATA_DIR), CLI_SECRET),
        [],
        "the CLI key reached FreeRoute's data directory"
      );
    }
  );

  await step("/authorize gets 501 consumer_subscription_pooling_disabled", async () => {
    // The Trae OAuth loopback callback stored query-string credentials with no
    // auth, so it is probed both without and with the management bearer.
    const query = "?code=e2e&state=e2e&access_token=e2e-must-not-store";
    for (const headers of [{}, mgmt()]) {
      const res = await fetch(`${fr.url}/authorize${query}`, { headers, redirect: "manual" });
      const body = await readJson(res);
      assert.equal(res.status, 501, JSON.stringify(body));
      assert.equal(body.error?.code, "consumer_subscription_pooling_disabled");
      assert.equal(typeof body.error?.message, "string");
    }
  });

  await step("FreeRoute listens on 127.0.0.1 only (non-loopback connect refused)", async () => {
    const ip = nonLoopbackIPv4();
    assert.ok(ip, "this machine has no non-loopback IPv4 address to probe");
    // Control: the probe can see a socket that IS bound on every interface.
    const control = createServer();
    await new Promise((r) => control.listen(0, "0.0.0.0", r));
    const controlOutcome = await tcpProbe(ip, control.address().port);
    await new Promise((r) => control.close(r));
    assert.equal(controlOutcome, "connected", `control probe to ${ip} did not connect`);

    assert.equal(await tcpProbe("127.0.0.1", fr.port), "connected", "loopback probe failed");
    const outcome = await tcpProbe(ip, fr.port);
    assert.equal(outcome, "ECONNREFUSED", `connect to ${ip}:${fr.port} gave ${outcome}`);
    log(`bind probe: ${ip}:${fr.port} -> ${outcome}`);
  });

  await step(
    "connection test passes with the OpenVault key and activates the connection",
    async () => {
      const res = await fetch(`${fr.url}/api/providers/${connection.id}/test`, {
        method: "POST",
        headers: mgmt(),
        body: "{}",
      });
      const body = await readJson(res);
      assert.equal(res.status, 200, JSON.stringify(body));
      assert.equal(body.valid, true, JSON.stringify(body));
      const list = await readJson(
        await fetch(`${fr.url}/api/providers?provider=${encodeURIComponent(node.id)}`, {
          headers: mgmt(),
        })
      );
      const row = list.connections?.find((c) => c.id === connection.id);
      assert.equal(row?.isActive, true, JSON.stringify(row));
      assert.ok(!row?.apiKey, "listing must not show an apiKey");
    }
  );

  const clientToken = await step(
    "client token minted in OpenVault via POST /api/apikeys",
    async () => {
      const { status, body } = await openVaultCall("POST", "/api/apikeys", {
        label: "freeroute-e2e-client",
      });
      assert.equal(status, 200, JSON.stringify(body));
      assert.ok(body.token);
      return body.token;
    }
  );

  const model = `${NODE_PREFIX}/${UPSTREAM_MODEL}`;

  await step(
    "chat completion through FreeRoute with the OpenVault client token returns the canned reply",
    async () => {
      upstreamCalls.length = 0;
      const { status, body } = await chat(fr.url, clientToken, model);
      assert.equal(status, 200, JSON.stringify(body));
      assert.equal(body.choices?.[0]?.message?.content, CANNED_CONTENT, JSON.stringify(body));
    }
  );

  await step("fake upstream received Authorization: Bearer <vault secret>", async () => {
    const chatCalls = upstreamCalls.filter((c) => c.path.endsWith("/chat/completions"));
    assert.ok(
      chatCalls.length > 0,
      `no chat call reached upstream: ${JSON.stringify(upstreamCalls)}`
    );
    assert.equal(chatCalls.at(-1).authorization, `Bearer ${PROVIDER_SECRET}`);
  });

  await step("OpenVault audited a secret_reveal for that key", async () => {
    const reveals = readAudit().filter((e) => e.event === "secret_reveal");
    assert.ok(reveals.length > auditBefore, "no new secret_reveal line in secret_audit.jsonl");
    assert.equal(reveals.at(-1).key_id, vaultKey.id);
    const auditText = readFileSync(join(OPENVAULT_HOME, "secret_audit.jsonl"), "utf8");
    assert.ok(!auditText.includes(PROVIDER_SECRET), "audit file must not contain the secret");
  });

  await step("a bogus client token gets 401", async () => {
    const { status, body } = await chat(fr.url, BOGUS_CLIENT_TOKEN, model);
    assert.equal(status, 401, JSON.stringify(body));
  });

  // The shapes FreeRoute's verify client (src/lib/netie/keyvault.ts) is written
  // against, taken from the real OpenVault rather than from a stub.
  await step(
    "OpenVault verify answers {ok,valid,key_id,tier} / {ok:true,valid:false}, and 401 without the admin token",
    async () => {
      const good = await openVaultCall("POST", "/api/apikeys/verify", { token: clientToken });
      assert.equal(good.status, 200, JSON.stringify(good.body));
      assert.equal(good.body.ok, true, JSON.stringify(good.body));
      assert.equal(good.body.valid, true, JSON.stringify(good.body));
      assert.equal(typeof good.body.key_id, "string", JSON.stringify(good.body));
      assert.equal(typeof good.body.tier, "string", JSON.stringify(good.body));
      for (const token of [BOGUS_CLIENT_TOKEN, "", "   "]) {
        const bad = await openVaultCall("POST", "/api/apikeys/verify", { token });
        assert.equal(bad.status, 200, JSON.stringify(bad.body));
        assert.deepEqual(bad.body, { ok: true, valid: false }, `token ${JSON.stringify(token)}`);
      }
      // Loopback alone is not enough: the call needs the admin token (or a listed service).
      const anonymous = await fetch(`${openVaultUrl}/api/apikeys/verify`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token: clientToken }),
      });
      assert.equal(anonymous.status, 401);
      assert.equal((await readJson(anonymous)).error?.type, "openvault_unauthenticated");
    }
  );

  await step("cursor/auto gets 501 consumer_subscription_pooling_disabled", async () => {
    const { status, body } = await chat(fr.url, clientToken, "cursor/auto");
    assert.equal(status, 501, JSON.stringify(body));
    assert.equal(body.error?.code, "consumer_subscription_pooling_disabled");
  });

  await step("claude-web/... gets 501 consumer_session_relay_disabled", async () => {
    const { status, body } = await chat(fr.url, clientToken, "claude-web/claude-sonnet-4");
    assert.equal(status, 501, JSON.stringify(body));
    assert.equal(body.error?.code, "consumer_session_relay_disabled");
  });

  await step(
    "tenant-only endpoint: no key is spent, the connection test fails, chat is not served",
    async () => {
      upstreamCalls.length = 0;
      const nodeRes = await fetch(`${fr.url}/api/provider-nodes`, {
        method: "POST",
        headers: mgmt(),
        body: JSON.stringify({
          type: "openai-compatible",
          name: "E2E tenant-only upstream",
          prefix: TENANT_NODE_PREFIX,
          apiType: "chat",
          baseUrl: tenantUpstreamUrl,
        }),
      });
      const nodeBody = await readJson(nodeRes);
      assert.equal(nodeRes.status, 201, JSON.stringify(nodeBody));
      const connRes = await fetch(`${fr.url}/api/providers`, {
        method: "POST",
        headers: mgmt(),
        body: JSON.stringify({ provider: nodeBody.node.id, name: "e2e-tenant-keyless" }),
      });
      const connBody = await readJson(connRes);
      assert.ok(connRes.status === 200 || connRes.status === 201, JSON.stringify(connBody));
      const conn = connBody.connection ?? connBody;

      const testRes = await fetch(`${fr.url}/api/providers/${conn.id}/test`, {
        method: "POST",
        headers: mgmt(),
        body: "{}",
      });
      const testBody = await readJson(testRes);
      assert.notEqual(
        testBody.valid,
        true,
        `tenant-only connection tested valid: ${JSON.stringify(testBody)}`
      );

      const { status, body } = await chat(
        fr.url,
        clientToken,
        `${TENANT_NODE_PREFIX}/${UPSTREAM_MODEL}`
      );
      assert.notEqual(status, 200, `tenant-only chat was served: ${JSON.stringify(body)}`);
      log(
        `tenant-only: test valid=${testBody.valid}, chat ${status} ${JSON.stringify(body).slice(0, 200)}`
      );
    }
  );

  await step("a tenant-custody key is never spent or revealed", async () => {
    const tenantSeen = allUpstreamRequests.filter((r) => r.authorization.includes(TENANT_SECRET));
    assert.equal(
      tenantSeen.length,
      0,
      `FreeRoute sent the tenant secret to the upstream: ${JSON.stringify(
        tenantSeen.map((r) => `${r.at} ${r.method} ${r.path}`)
      )}\nall upstream requests: ${JSON.stringify(
        allUpstreamRequests.map((r) => `${r.at} ${r.method} ${r.path} ${r.authorization.replace(/(sk-e2e-[a-z]+-secret-).*/, "$1...")}`),
        null,
        1
      )}`
    );
    assert.ok(
      allUpstreamAuth.includes(`Bearer ${PROVIDER_SECRET}`),
      "self-check: the pooled secret did reach the upstream from FreeRoute"
    );
    const probedTenant = openVaultProbes.filter((r) => r.authorization.includes(TENANT_SECRET));
    log(
      `OpenVault's own chat probe reached the fake upstream ${openVaultProbes.length} time(s), ` +
        `${probedTenant.length} with a tenant key (not FreeRoute traffic, not counted above)`
    );
    const tenantIds = new Set(tenantKeys.map((k) => k.id));
    const reveals = readAudit().filter((e) => e.event === "secret_reveal");
    const tenantReveals = reveals.filter((e) => tenantIds.has(e.key_id));
    assert.deepEqual(tenantReveals, [], "OpenVault audited a reveal of a tenant key");
    assert.ok(
      reveals.some((e) => e.key_id === vaultKey.id),
      "self-check: the pooled key reveal is in the audit"
    );
  });

  // OpenVault admits 60 verify calls a minute per caller. The test and FreeRoute
  // present the same admin token, so they share one bucket: spend it here, then a
  // client token FreeRoute has not seen (not in its 60 s cache) must fail closed
  // and the log must name the cause. Runs after the steps that need a good verify.
  await step(
    "OpenVault rate-limits verify (429): FreeRoute fails closed (401) and logs openvault_verify_rate_limited",
    async () => {
      const fresh = await openVaultCall("POST", "/api/apikeys", { label: "freeroute-e2e-limited" });
      assert.equal(fresh.status, 200, JSON.stringify(fresh.body));
      let limited = null;
      for (let call = 0; call < 130 && !limited; call += 1) {
        const res = await fetch(`${openVaultUrl}/api/apikeys/verify`, {
          method: "POST",
          headers: { "content-type": "application/json", "X-OpenVault-Admin": adminToken() },
          body: JSON.stringify({ token: fresh.body.token }),
        });
        if (res.status === 429) limited = await readJson(res);
      }
      assert.deepEqual(limited, { detail: "too many verify requests" });
      const { status, body } = await chat(fr.url, fresh.body.token, model);
      assert.equal(status, 401, JSON.stringify(body));
      // The log is piped to a file, so give the write a moment to land.
      let logText = "";
      for (let wait = 0; wait < 50; wait += 1) {
        logText = readFileSync(fr.child.logPath, "utf8");
        if (/openvault_verify_rate_limited/.test(logText)) break;
        await sleep(100);
      }
      assert.match(logText, /openvault_verify_rate_limited/);
    }
  );

  // Last of the steps that need the planted connection: an import replaces the
  // whole database. The backup is a real snapshot of the live database (exported
  // through the API) with a known secret planted in every place a key can hide;
  // after the import, the scrubbed live file must hold none of them.
  await step(
    "importing a SQLite backup that carries known secrets leaves none of them in the live database file",
    async () => {
      const exported = await fetch(`${fr.url}/api/db-backups/export`, { headers: mgmt() });
      assert.equal(exported.status, 200);
      const importPath = join(TMP, "import-with-secrets.sqlite");
      writeFileSync(importPath, Buffer.from(await exported.arrayBuffer()));

      const { DatabaseSync } = await import("node:sqlite");
      const planted = new DatabaseSync(importPath);
      const now = new Date().toISOString();
      try {
        planted.exec("PRAGMA journal_mode = DELETE");
        planted
          .prepare(
            "INSERT INTO provider_connections (id, provider, auth_type, name, priority, is_active, api_key, provider_specific_data, created_at, updated_at) " +
              "VALUES ('e2e-import-conn', 'openai', 'apikey', 'e2e-import', 1, 1, ?, ?, ?, ?)"
          )
          .run(
            IMPORT_SECRETS.apiKey,
            JSON.stringify({ extraApiKeys: [IMPORT_SECRETS.extraKey] }),
            now,
            now
          );
        planted
          .prepare(
            "INSERT OR REPLACE INTO cloud_agent_credentials (provider_id, api_key_encrypted, base_url, updated_at) VALUES ('devin', ?, NULL, ?)"
          )
          .run(IMPORT_SECRETS.agentKey, now);
        planted
          .prepare(
            "INSERT INTO api_keys (id, name, key, machine_id, created_at) VALUES ('e2e-import-client', 'e2e-import', ?, NULL, ?)"
          )
          .run(IMPORT_SECRETS.clientKey, now);
        planted
          .prepare(
            "INSERT OR REPLACE INTO key_value (namespace, key, value) VALUES ('databaseSettings', 'cache.semanticCacheEmbeddingApiKey', ?)"
          )
          .run(JSON.stringify(IMPORT_SECRETS.embeddingKey));
      } finally {
        planted.close();
      }

      // Self-check: the file being uploaded really carries every planted secret.
      const upload = readFileSync(importPath);
      for (const [where, secret] of Object.entries(IMPORT_SECRETS)) {
        assert.ok(upload.includes(Buffer.from(secret)), `self-check: ${where} not in the upload`);
      }

      // Control: the byte check can see what an ordinary clear leaves behind. A
      // copy of the upload gets plain UPDATE and DELETE statements (no
      // secure_delete, no VACUUM); the cells they freed still hold the secrets.
      // Without this, a clean live file could only mean the check is blind.
      const controlPath = join(TMP, "plain-clear-control.sqlite");
      writeFileSync(controlPath, upload);
      const control = new DatabaseSync(controlPath);
      try {
        control.exec("PRAGMA journal_mode = DELETE");
        control.exec("PRAGMA secure_delete = OFF");
        control.exec(
          "UPDATE provider_connections SET api_key = NULL, provider_specific_data = NULL; " +
            "DELETE FROM api_keys; DELETE FROM cloud_agent_credentials; " +
            "DELETE FROM key_value WHERE key = 'cache.semanticCacheEmbeddingApiKey';"
        );
      } finally {
        control.close();
      }
      const plainCleared = readFileSync(controlPath);
      const remnants = Object.entries(IMPORT_SECRETS).filter(([, secret]) =>
        plainCleared.includes(Buffer.from(secret))
      );
      assert.ok(remnants.length > 0, "control: a plain clear left no remnant, the byte check is blind");
      log(`control: a plain clear leaves ${remnants.map(([where]) => where).join(", ")} in the file`);

      const res = await fetch(`${fr.url}/api/db-backups/import?filename=e2e-import.sqlite`, {
        method: "POST",
        headers: mgmt({ "content-type": "application/octet-stream" }),
        body: upload,
      });
      const body = await readJson(res);
      assert.equal(res.status, 200, JSON.stringify(body));
      assert.equal(body.imported, true, JSON.stringify(body));

      // The import took effect (the planted connection is there) and holds no key.
      const list = await readJson(
        await fetch(`${fr.url}/api/providers?provider=openai`, { headers: mgmt() })
      );
      const row = list.connections?.find((c) => c.id === "e2e-import-conn");
      assert.ok(row, `the imported connection is missing: ${JSON.stringify(list)}`);
      assert.ok(!row.apiKey, "the imported connection kept an apiKey");
      const agents = await readJson(
        await fetch(`${fr.url}/api/v1/agents/credentials`, { headers: mgmt() })
      );
      assert.deepEqual(agents.data, [], `an agent key survived the import: ${JSON.stringify(agents)}`);

      // The live file, its WAL and its shm, read as bytes, while FreeRoute still has them open.
      const live = filesUnder(DATA_DIR).filter((p) => /storage\.sqlite(-wal|-shm)?$/.test(p));
      assert.ok(
        live.some((p) => p.endsWith("storage.sqlite") && statSync(p).size > 0),
        `no live storage.sqlite in ${DATA_DIR}`
      );
      for (const [where, secret] of Object.entries(IMPORT_SECRETS)) {
        assert.deepEqual(filesContaining(live, secret), [], `${where} is in the live database`);
      }
      // The scrubbed upload copy in the OS temp directory is gone as well.
      const leftovers = readdirSync(tmpdir())
        .filter((name) => /^omniroute-import-.*\.sqlite$/.test(name))
        .map((name) => join(tmpdir(), name));
      for (const secret of Object.values(IMPORT_SECRETS)) {
        assert.deepEqual(filesContaining(leftovers, secret), [], "the upload copy kept a secret");
      }
      log(`import scrub: checked ${live.map((p) => p.slice(DATA_DIR.length + 1)).join(", ")}`);
    }
  );

  await stopProcess(fr.child);

  // A wrong admin token: a fresh FreeRoute (empty 60s caches) reading a
  // token file that does not match this OpenVault. The management bearer is
  // used as the client credential, because an OpenVault client token cannot
  // be verified without the admin token either.
  const wrongTokenPath = join(TMP, "wrong_admin_token");
  writeFileSync(wrongTokenPath, `${randomBytes(32).toString("hex")}\n`, { mode: 0o600 });
  fr = await step("FreeRoute restarts with a wrong admin token file", () =>
    startFreeRoute("freeroute-2-wrong-token", { adminTokenPath: wrongTokenPath })
  );
  await step("wrong admin token gets 503 openvault_admin_token_rejected", async () => {
    const { status, body } = await chat(fr.url, MANAGEMENT_API_KEY, model);
    assert.equal(status, 503, JSON.stringify(body));
    assert.equal(body.error?.code, "openvault_admin_token_rejected", JSON.stringify(body));
    assert.ok(
      !JSON.stringify(body).includes(readFileSync(wrongTokenPath, "utf8").trim()),
      "token leaked"
    );
  });
  await step(
    "wrong admin token: an OpenVault client token is refused (401, fail closed)",
    async () => {
      const { status } = await chat(fr.url, clientToken, model);
      assert.equal(status, 401);
    }
  );
  await stopProcess(fr.child);

  await step("OpenVault stops", () => stopProcess(openVault));
  fr = await step("FreeRoute restarts with OpenVault down (fresh caches)", () =>
    startFreeRoute("freeroute-3-vault-down", { adminTokenPath })
  );
  await step("OpenVault down gets 503 openvault_keyvault_unreachable", async () => {
    const { status, body } = await chat(fr.url, MANAGEMENT_API_KEY, model);
    assert.equal(status, 503, JSON.stringify(body));
    assert.equal(body.error?.code, "openvault_keyvault_unreachable", JSON.stringify(body));
  });
  await stopProcess(fr.child);

  await step(
    "FreeRoute DATA_DIR and logs contain none of the provider, tenant, bulk, agent, CLI or imported secrets",
    async () => {
      const dataFiles = filesUnder(DATA_DIR);
      assert.ok(
        dataFiles.some((p) => /\.(sqlite|db)$/.test(p)),
        `no sqlite file in ${DATA_DIR}`
      );
      const freeRouteLogs = filesUnder(LOG_DIR).filter((p) => /freeroute-/.test(p));
      for (const secret of [
        PROVIDER_SECRET,
        TENANT_SECRET,
        BULK_SECRET,
        AGENT_SECRET,
        CLI_SECRET,
        ...Object.values(IMPORT_SECRETS),
      ]) {
        const hits = filesContaining([...dataFiles, ...freeRouteLogs], secret);
        assert.deepEqual(hits, [], `secret found in: ${hits.join(", ")}`);
      }
      // Sanity: the grep can find what it looks for. The connection id is in the DB.
      assert.ok(filesContaining(dataFiles, connection.id).length > 0, "grep self-check failed");
      log(`grepped ${dataFiles.length} data files and ${freeRouteLogs.length} log files`);
    }
  );

  await step(
    "the call-log artifact worker did not fail (its files were in the grep above)",
    async () => {
      const freeRouteLogs = filesUnder(LOG_DIR).filter((p) => /freeroute-/.test(p));
      const failed = filesContaining(freeRouteLogs, "Call-log artifact worker failed");
      assert.deepEqual(failed, [], `call-log worker failed in: ${failed.join(", ")}`);
    }
  );
}

let exitCode = 0;
const onSignal = async () => {
  await cleanup();
  process.exit(130);
};
process.on("SIGINT", onSignal);
process.on("SIGTERM", onSignal);

try {
  await main();
} catch {
  exitCode = 1;
  for (const child of children) log(`--- ${child.label} log tail ---\n${tail(child.logPath, 40)}`);
} finally {
  await cleanup();
}
const passed = results.filter((r) => r.ok).length;
log(`${passed}/${results.length} steps passed${exitCode ? ", FAILED" : ""}`);
log(existsSync(TMP) ? `temp root still present: ${TMP}` : "temp root removed");
process.exit(exitCode);
