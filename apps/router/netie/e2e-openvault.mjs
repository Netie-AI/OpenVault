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
//      into FreeRoute, a wrong admin token, OpenVault stopped.
//   8. Greps FreeRoute's DATA_DIR and logs for the provider secret.
// Every process and temp dir is removed on exit, pass or fail.
//
// Run: npm run test:e2e

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
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
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { randomBytes } from "node:crypto";
import { setTimeout as sleep } from "node:timers/promises";

const ROOT = resolve(new URL("..", import.meta.url).pathname);
const OPENMW_DIR = resolve(process.env.OPENVAULT_OPENMW_DIR || join(ROOT, "..", "..", "OpenMW"));

const PROVIDER_SECRET = `sk-e2e-vault-secret-${randomBytes(12).toString("hex")}`;
const MANAGEMENT_API_KEY = `sk-e2e-mgmt-${randomBytes(16).toString("hex")}`;
const BOGUS_CLIENT_TOKEN = `ov_bogus_${randomBytes(16).toString("hex")}`;
const CANNED_CONTENT = `canned-e2e-reply-${randomBytes(4).toString("hex")}`;
const UPSTREAM_MODEL = "gpt-e2e";
const NODE_PREFIX = "e2e";

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

function startFakeUpstream() {
  return new Promise((resolveServer, reject) => {
    const srv = createServer((req, res) => {
      let body = "";
      req.on("data", (chunk) => (body += chunk));
      req.on("end", () => {
        const path = new URL(req.url, "http://127.0.0.1").pathname;
        upstreamCalls.push({ method: req.method, path, authorization: req.headers.authorization });
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
  const child = startProcess(name, process.execPath, ["scripts/dev/run-next.mjs", "start"], {
    cwd: ROOT,
    env: {
      ...process.env,
      PORT: String(port),
      DASHBOARD_PORT: String(port),
      API_PORT: String(port),
      OMNIROUTE_HOSTNAME: "127.0.0.1",
      HOSTNAME: "127.0.0.1",
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
  return { child, url };
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

  // Baseline before FreeRoute first asks OpenVault for the secret. The 60s
  // secret cache means the chat below may reuse the reveal the test made.
  const auditBefore = readAudit().filter((e) => e.event === "secret_reveal").length;

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

  await step("FreeRoute DATA_DIR and logs do not contain the provider secret", async () => {
    const dataFiles = filesUnder(DATA_DIR);
    assert.ok(
      dataFiles.some((p) => /\.(sqlite|db)$/.test(p)),
      `no sqlite file in ${DATA_DIR}`
    );
    const freeRouteLogs = filesUnder(LOG_DIR).filter((p) => /freeroute-/.test(p));
    const hits = filesContaining([...dataFiles, ...freeRouteLogs], PROVIDER_SECRET);
    assert.deepEqual(hits, [], `secret found in: ${hits.join(", ")}`);
    // Sanity: the grep can find what it looks for. The connection id is in the DB.
    assert.ok(filesContaining(dataFiles, connection.id).length > 0, "grep self-check failed");
    log(`grepped ${dataFiles.length} data files and ${freeRouteLogs.length} log files`);
  });

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
