// Copyright (c) 2026 Netie AI. MIT.
//
// FreeRoute smoke test, builds confidence that a *running* server enforces
// the three named hard-disables (open-sse/netie/policy.ts), the
// keys_managed_by_openvault 501 (both provider keys and the router's own
// client keys), and OpenVault-issued client token verification, end to end
// over real HTTP.
//
// Starts the production server (`npm start`, the same `next start` path
// `npm run build`/`npm run build:backend` produces) on a free loopback port,
// with a temp DATA_DIR and OPENVAULT_URL pointed at a tiny stub HTTP server
// this test also starts. REQUIRE_API_KEY is turned on so the bogus-token and
// management-auth assertions are meaningful; OMNIROUTE_API_KEY is a random
// per-run value used as the "management" bearer (isConfiguredEnvApiKey path
// in src/lib/db/apiKeys.ts).
//
// The OpenVault stub enforces X-OpenVault-Admin like real OpenVault: every
// route under /api/keys, /api/keyvault, /api/apikeys, /api/secrets,
// /api/vault and /keys answers 401 openvault_unauthenticated when the header
// is missing or wrong. The token is random per run, written to a mode-0600
// temp file, and passed to the server as OPENVAULT_ADMIN_TOKEN_PATH.
//
// Run: npm run build:backend && npm run test:smoke

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer, request as httpRequest } from "node:http";
import { connect as netConnect } from "node:net";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { networkInterfaces, tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import { setTimeout as sleep } from "node:timers/promises";

const ROOT = new URL("..", import.meta.url).pathname;

const MANAGEMENT_API_KEY = `sk-smoke-mgmt-${randomBytes(16).toString("hex")}`;
const OPENVAULT_VALID_TOKEN = `ovtok_smoke_${randomBytes(16).toString("hex")}`;
const OPENVAULT_BOGUS_TOKEN = `ovtok_bogus_${randomBytes(16).toString("hex")}`;
const OPENVAULT_KEY_ID = "key_smoke_test";
const OPENVAULT_VERIFY_KEY_ID = "apikey_smoke_test";
const OPENVAULT_ADMIN_TOKEN = `adm_smoke_${randomBytes(24).toString("hex")}`;
const OPENVAULT_ADMIN_ROOTS = [
  "/api/keys",
  "/api/keyvault",
  "/api/apikeys",
  "/api/secrets",
  "/api/vault",
  "/keys",
];

let stubServer;
let stubPort;
let serverProcess;
let appPort;
let dataDir;
let adminTokenDir;
const serverLogs = [];
let verifyCallCount = 0;
let adminRejectedCount = 0;

function pathNeedsAdmin(pathname) {
  return OPENVAULT_ADMIN_ROOTS.some((root) => pathname === root || pathname.startsWith(`${root}/`));
}

/** Minimal OpenVault stub: /api/keys, /api/keys/{id}/secret, /api/apikeys/verify. */
function startOpenVaultStub() {
  return new Promise((resolve, reject) => {
    const server = createServer((req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      res.setHeader("content-type", "application/json");

      if (
        pathNeedsAdmin(url.pathname) &&
        req.headers["x-openvault-admin"] !== OPENVAULT_ADMIN_TOKEN
      ) {
        adminRejectedCount += 1;
        res.writeHead(401);
        res.end(
          JSON.stringify({ error: { message: "unauthorized", type: "openvault_unauthenticated" } })
        );
        return;
      }

      if (url.pathname === "/api/keys" && req.method === "GET") {
        res.writeHead(200);
        res.end(
          JSON.stringify({
            keys: [
              {
                id: OPENVAULT_KEY_ID,
                label: "smoke-test",
                provider: "openai",
                role: "chat",
                base_url: null,
                masked_secret: "sk-****test",
                enabled: true,
                priority: 10,
                precheck_status: "ok",
                account_id: "smoke",
                lifecycle: "active",
                // Real OpenVault custody values are "pooled" and "tenant"; the
                // router spends only pooled (or untagged) keys.
                custody: "pooled",
              },
            ],
          })
        );
        return;
      }

      if (url.pathname === `/api/keys/${OPENVAULT_KEY_ID}/secret` && req.method === "GET") {
        if (req.headers["x-openvault-reveal"] !== "intentional") {
          res.writeHead(403);
          res.end(JSON.stringify({ error: "reveal header required" }));
          return;
        }
        res.writeHead(200);
        res.end(JSON.stringify({ id: OPENVAULT_KEY_ID, secret: "sk-smoke-test-not-real" }));
        return;
      }

      if (url.pathname === "/api/apikeys/verify" && req.method === "POST") {
        verifyCallCount += 1;
        let body = "";
        req.on("data", (chunk) => (body += chunk));
        req.on("end", () => {
          let token = null;
          try {
            token = JSON.parse(body || "{}").token ?? null;
          } catch {
            /* malformed body -> invalid */
          }
          res.writeHead(200);
          if (token === OPENVAULT_VALID_TOKEN) {
            res.end(JSON.stringify({ valid: true, key_id: OPENVAULT_VERIFY_KEY_ID, tier: "free" }));
          } else {
            res.end(JSON.stringify({ valid: false }));
          }
        });
        return;
      }

      res.writeHead(404);
      res.end(JSON.stringify({ error: "not found in stub" }));
    });
    server.listen(0, "127.0.0.1", () => resolve(server));
    server.on("error", reject);
  });
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
    srv.on("error", reject);
  });
}

async function waitForHealth(port, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/healthz`);
      if (res.status === 200) return;
      lastError = new Error(`healthz status ${res.status}`);
    } catch (err) {
      lastError = err;
    }
    await sleep(1000);
  }
  throw new Error(
    `Server never became healthy on port ${port}: ${lastError?.message}\n--- server output (tail) ---\n${serverLogs.slice(-160).join("")}`
  );
}

before(async () => {
  stubServer = await startOpenVaultStub();
  stubPort = stubServer.address().port;

  appPort = await getFreePort();
  dataDir = mkdtempSync(join(tmpdir(), "freeroute-smoke-"));
  adminTokenDir = mkdtempSync(join(tmpdir(), "freeroute-smoke-ovadmin-"));
  const adminTokenPath = join(adminTokenDir, "admin_token");
  writeFileSync(adminTokenPath, `${OPENVAULT_ADMIN_TOKEN}\n`, { mode: 0o600 });

  // Start with no bind override so the default bind address is what gets
  // tested. HOSTNAME is set to a non-loopback value to prove the shell's
  // HOSTNAME variable does not move the bind.
  const parentEnv = { ...process.env };
  delete parentEnv.HOST;
  delete parentEnv.OMNIROUTE_HOSTNAME;
  delete parentEnv.OMNIROUTE_SERVER_HOST;
  serverProcess = spawn(process.execPath, ["scripts/dev/run-next.mjs", "start"], {
    cwd: ROOT,
    env: {
      ...parentEnv,
      PORT: String(appPort),
      DASHBOARD_PORT: String(appPort),
      API_PORT: String(appPort),
      HOSTNAME: "0.0.0.0",
      DATA_DIR: dataDir,
      OPENVAULT_URL: `http://127.0.0.1:${stubPort}`,
      OPENVAULT_ADMIN_TOKEN_PATH: adminTokenPath,
      NODE_ENV: "production",
      DISABLE_SQLITE_AUTO_BACKUP: "true",
      OMNIROUTE_DISABLE_BACKGROUND_SERVICES: "true",
      OMNIROUTE_DISABLE_LOCAL_HEALTHCHECK: "true",
      OMNIROUTE_DISABLE_TOKEN_HEALTHCHECK: "true",
      // A real inbound-client bearer with "manage" scope (isConfiguredEnvApiKey
      // path in src/lib/db/apiKeys.ts), used to authenticate the management
      // assertions below without needing dashboard-session/onboarding state.
      OMNIROUTE_API_KEY: MANAGEMENT_API_KEY,
      // Required so the anonymous/bogus-bearer CLIENT_API path actually 401s
      // instead of degrading to anonymous (src/server/authz/policies/clientApi.ts).
      REQUIRE_API_KEY: "true",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  serverProcess.stdout.on("data", (chunk) => serverLogs.push(chunk.toString()));
  serverProcess.stderr.on("data", (chunk) => serverLogs.push(chunk.toString()));

  await waitForHealth(appPort);
});

after(async () => {
  if (serverProcess && !serverProcess.killed) {
    serverProcess.kill("SIGTERM");
    await sleep(1000);
    if (!serverProcess.killed) serverProcess.kill("SIGKILL");
  }
  if (stubServer) await new Promise((resolve) => stubServer.close(resolve));
  for (const dir of [dataDir, adminTokenDir]) {
    if (!dir) continue;
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      /* best-effort cleanup */
    }
  }
});

function baseUrl() {
  return `http://127.0.0.1:${appPort}`;
}

function managementHeaders(extra = {}) {
  return { authorization: `Bearer ${MANAGEMENT_API_KEY}`, ...extra };
}

async function readJson(res) {
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { __rawBody: text };
  }
}

test("the server binds 127.0.0.1 by default", async () => {
  const logs = serverLogs.join("");
  assert.match(logs, new RegExp(`listening on http://127\\.0\\.0\\.1:${appPort}\\b`));
  assert.doesNotMatch(logs, /listening on http:\/\/0\.0\.0\.0:/);

  // A non-loopback address of this machine must refuse the connection.
  const external = Object.values(networkInterfaces())
    .flat()
    .find((iface) => iface && iface.family === "IPv4" && !iface.internal);
  if (!external) return; // no non-loopback interface on this host; the log check above still ran
  const outcome = await new Promise((resolve) => {
    const socket = netConnect({ host: external.address, port: appPort });
    socket.setTimeout(3000);
    socket.once("connect", () => {
      socket.destroy();
      resolve("connected");
    });
    socket.once("timeout", () => {
      socket.destroy();
      resolve("timeout");
    });
    socket.once("error", (err) => resolve(err.code || "error"));
  });
  assert.notEqual(outcome, "connected", `server accepted a connection on ${external.address}`);
});

test("health endpoint responds 200", async () => {
  const res = await fetch(`${baseUrl()}/healthz`);
  assert.equal(res.status, 200);
});

test("OAuth device/browser login start is hard-disabled (consumer_subscription_pooling_disabled)", async () => {
  const res = await fetch(`${baseUrl()}/api/oauth/codex/authorize`);
  assert.equal(res.status, 501);
  const body = await readJson(res);
  assert.equal(body.error?.code, "consumer_subscription_pooling_disabled");
  assert.equal(typeof body.error?.message, "string");
});

test("MITM/session-relay subsystem route is hard-disabled (consumer_session_relay_disabled)", async () => {
  const res = await fetch(`${baseUrl()}/api/settings/mitm`);
  assert.equal(res.status, 501);
  const body = await readJson(res);
  assert.equal(body.error?.code, "consumer_session_relay_disabled");
});

test("chat completion targeting an oauth-provider model is hard-disabled", async () => {
  const res = await fetch(`${baseUrl()}/api/v1/chat/completions`, {
    method: "POST",
    headers: managementHeaders({ "content-type": "application/json" }),
    body: JSON.stringify({
      model: "cursor/auto",
      messages: [{ role: "user", content: "hello" }],
    }),
  });
  assert.equal(res.status, 501);
  const body = await readJson(res);
  assert.equal(body.error?.code, "consumer_subscription_pooling_disabled");
});

test("saving a provider API key returns keys_managed_by_openvault (authenticated as management)", async () => {
  const res = await fetch(`${baseUrl()}/api/providers`, {
    method: "POST",
    headers: managementHeaders({ "content-type": "application/json" }),
    body: JSON.stringify({
      provider: "openai",
      name: "smoke-test-connection",
      apiKey: "sk-should-never-be-stored",
    }),
  });
  assert.equal(res.status, 501);
  const body = await readJson(res);
  assert.equal(body.error?.code, "keys_managed_by_openvault");
  assert.match(body.error?.message ?? "", /127\.0\.0\.1:3010\/keys/);
});

test("minting a router-side client key returns keys_managed_by_openvault", async () => {
  const res = await fetch(`${baseUrl()}/api/keys`, {
    method: "POST",
    headers: managementHeaders({ "content-type": "application/json" }),
    body: JSON.stringify({ name: "should-not-be-minted" }),
  });
  assert.equal(res.status, 501);
  const body = await readJson(res);
  assert.equal(body.error?.code, "keys_managed_by_openvault");
});

test("an OpenVault-verified client token is accepted on /v1/models (non-401)", async () => {
  const res = await fetch(`${baseUrl()}/api/v1/models`, {
    headers: { authorization: `Bearer ${OPENVAULT_VALID_TOKEN}` },
  });
  assert.notEqual(res.status, 401, `expected non-401, got ${res.status}: ${await res.text()}`);
  assert.ok(verifyCallCount > 0, "expected the server to call the OpenVault verify stub");
  assert.equal(adminRejectedCount, 0, "server must send the OpenVault admin token on every call");
});

test("a bogus client token is rejected on /v1/models (401)", async () => {
  const res = await fetch(`${baseUrl()}/api/v1/models`, {
    headers: { authorization: `Bearer ${OPENVAULT_BOGUS_TOKEN}` },
  });
  assert.equal(res.status, 401);
});

test("checking for an update is hard-disabled (upstream_updates_disabled)", async () => {
  const res = await fetch(`${baseUrl()}/api/system/version`, {
    headers: managementHeaders(),
  });
  assert.equal(res.status, 501);
  const body = await readJson(res);
  assert.equal(body.error?.code, "upstream_updates_disabled");
  assert.equal(typeof body.error?.message, "string");
});

test("the 9router embedded-service integration is hard-disabled (upstream_service_not_included)", async () => {
  const res = await fetch(`${baseUrl()}/api/services/9router/status`, {
    headers: managementHeaders(),
  });
  assert.equal(res.status, 501);
  const body = await readJson(res);
  assert.equal(body.error?.code, "upstream_service_not_included");
  assert.equal(typeof body.error?.message, "string");
});

// ── FreeRoute security regressions (fix-router-security) ────────────────────
// Each case below was reachable before; each must now answer its named 501.

async function expectNamed(res, status, code) {
  const body = await readJson(res);
  assert.equal(res.status, status, `expected ${status} ${code}, got ${res.status}: ${JSON.stringify(body)}`);
  assert.equal(body.error?.code, code);
  assert.equal(typeof body.error?.message, "string");
  return body;
}

function postJson(path, body, headers = {}) {
  return fetch(`${baseUrl()}${path}`, {
    method: "POST",
    headers: managementHeaders({ "content-type": "application/json", ...headers }),
    body: JSON.stringify(body),
  });
}

test("GET /authorize (Trae OAuth callback, no auth) does not store a connection", async () => {
  const url = new URL(`${baseUrl()}/authorize`);
  url.searchParams.set("userJwt", JSON.stringify({ Token: "trae-fake-token", RefreshToken: "trae-rt" }));
  url.searchParams.set("userInfo", JSON.stringify({ UserID: "u1" }));
  await expectNamed(await fetch(url), 501, "consumer_subscription_pooling_disabled");
});

test("a browser-session provider cannot be created, even without a key", async () => {
  await expectNamed(
    await postJson("/api/providers", { provider: "copilot-web", name: "smoke-copilot-web" }),
    501,
    "consumer_session_relay_disabled"
  );
});

test("browser login capture and manual OAuth refresh are hard-disabled per connection", async () => {
  await expectNamed(
    await postJson("/api/providers/any-connection-id/login", { timeout: 3000 }),
    501,
    "consumer_session_relay_disabled"
  );
  await expectNamed(
    await postJson("/api/providers/any-connection-id/refresh", {}),
    501,
    "consumer_subscription_pooling_disabled"
  );
  await expectNamed(
    await postJson("/api/providers/any-connection-id/refresh-cursor", {}),
    501,
    "consumer_subscription_pooling_disabled"
  );
});

test("bulk add and file import refuse provider keys", async () => {
  await expectNamed(
    await postJson("/api/providers/bulk", {
      provider: "openai",
      entries: [{ name: "smoke-bulk", apiKey: "sk-should-never-be-stored" }],
    }),
    501,
    "keys_managed_by_openvault"
  );
  await expectNamed(
    await postJson("/api/providers/import", {
      entries: [{ provider: "openai", name: "smoke-import", apiKey: "sk-should-never-be-stored" }],
    }),
    501,
    "keys_managed_by_openvault"
  );
  await expectNamed(
    await postJson("/api/providers/command-code/auth/apply", { state: "x" }),
    501,
    "keys_managed_by_openvault"
  );
});

test("an API-key connection is created without a key, and PUT/PATCH refuse one", async () => {
  const created = await postJson("/api/providers", { provider: "openai", name: "smoke-keyless" });
  const createdBody = await readJson(created);
  assert.equal(created.status, 201, JSON.stringify(createdBody));
  const id = createdBody.connection?.id;
  assert.ok(id, "created connection id");

  for (const method of ["PUT", "PATCH"]) {
    const res = await fetch(`${baseUrl()}/api/providers/${id}`, {
      method,
      headers: managementHeaders({ "content-type": "application/json" }),
      body: JSON.stringify({ apiKey: "sk-should-never-be-stored" }),
    });
    await expectNamed(res, 501, "keys_managed_by_openvault");
  }
  const extra = await fetch(`${baseUrl()}/api/providers/${id}`, {
    method: "PUT",
    headers: managementHeaders({ "content-type": "application/json" }),
    body: JSON.stringify({ providerSpecificData: { extraApiKeys: ["sk-should-never-be-stored"] } }),
  });
  await expectNamed(extra, 501, "keys_managed_by_openvault");

  // A plain rename still works.
  const rename = await fetch(`${baseUrl()}/api/providers/${id}`, {
    method: "PUT",
    headers: managementHeaders({ "content-type": "application/json" }),
    body: JSON.stringify({ name: "smoke-keyless-renamed" }),
  });
  assert.equal(rename.status, 200);
});

test("cookie validation for a browser-session provider is refused", async () => {
  await expectNamed(
    await postJson("/api/providers/validate", { provider: "grok-web", apiKey: "sso=abc" }),
    501,
    "consumer_session_relay_disabled"
  );
});

test("TLS fingerprint stealth cannot be switched on", async () => {
  const res = await fetch(`${baseUrl()}/api/settings/feature-flags`, {
    method: "PUT",
    headers: managementHeaders({ "content-type": "application/json" }),
    body: JSON.stringify({ key: "ENABLE_TLS_FINGERPRINT", value: "true" }),
  });
  await expectNamed(res, 501, "tls_fingerprint_stealth_disabled");
});

test("cloud credential sync cannot write tokens into a local connection", async () => {
  const res = await fetch(`${baseUrl()}/api/cloud/credentials/update`, {
    method: "PUT",
    headers: managementHeaders({ "content-type": "application/json" }),
    body: JSON.stringify({ provider: "openai", credentials: { accessToken: "at-should-never-be-stored" } }),
  });
  await expectNamed(res, 501, "keys_managed_by_openvault");
});

test("/v1/messages, /v1/responses and /v1/completions return the named 501 for subscription models", async () => {
  await expectNamed(
    await postJson("/api/v1/messages", {
      model: "claude/claude-sonnet-4-5",
      max_tokens: 5,
      messages: [{ role: "user", content: "hi" }],
    }),
    501,
    "consumer_subscription_pooling_disabled"
  );
  await expectNamed(
    await postJson("/api/v1/responses", { model: "codex/gpt-5.5", input: "hi" }),
    501,
    "consumer_subscription_pooling_disabled"
  );
  await expectNamed(
    await postJson("/api/v1/completions", { model: "cursor/auto", prompt: "hi" }),
    501,
    "consumer_subscription_pooling_disabled"
  );
});

test("local-CLI subscription executors and the browser-fingerprint provider are refused", async () => {
  for (const model of ["dva/x", "aug/x", "zc/x", "cxa/gpt-5.5"]) {
    await expectNamed(
      await postJson("/api/v1/chat/completions", { model, messages: [{ role: "user", content: "hi" }] }),
      501,
      "consumer_subscription_pooling_disabled"
    );
  }
  await expectNamed(
    await postJson("/api/v1/chat/completions", { model: "cfp/x", messages: [{ role: "user", content: "hi" }] }),
    501,
    "tls_fingerprint_stealth_disabled"
  );
});

test("media routes refuse session-relay and subscription providers", async () => {
  await expectNamed(
    await postJson("/api/v1/images/generations", { model: "uc/flux", prompt: "x" }),
    501,
    "consumer_session_relay_disabled"
  );
  await expectNamed(
    await postJson("/api/v1/images/generations", { model: "codex/gpt-image-1", prompt: "x" }),
    501,
    "consumer_subscription_pooling_disabled"
  );
  await expectNamed(
    await postJson("/api/v1/videos/generations", { model: "veoaifree-web/veo", prompt: "x" }),
    501,
    "consumer_session_relay_disabled"
  );
  await expectNamed(
    await postJson("/api/v1/music/generations", { model: "udio/udio", prompt: "x" }),
    501,
    "consumer_session_relay_disabled"
  );
  await expectNamed(
    await postJson("/api/v1/audio/speech", { model: "uc/tts-1", input: "hi" }),
    501,
    "consumer_session_relay_disabled"
  );
});

test("the Responses WebSocket upgrade answers the named 501", async () => {
  const { status, body } = await new Promise((resolve, reject) => {
    const req = httpRequest({
      host: "127.0.0.1",
      port: appPort,
      path: "/v1/responses",
      headers: {
        Connection: "Upgrade",
        Upgrade: "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": randomBytes(16).toString("base64"),
        authorization: `Bearer ${MANAGEMENT_API_KEY}`,
      },
    });
    req.on("response", (res) => {
      let text = "";
      res.on("data", (chunk) => (text += chunk));
      res.on("end", () => resolve({ status: res.statusCode, body: text }));
    });
    req.on("upgrade", () => reject(new Error("WebSocket upgrade must not succeed")));
    req.on("error", reject);
    req.end();
  });
  assert.equal(status, 501, body);
  assert.equal(JSON.parse(body).error?.code, "consumer_subscription_pooling_disabled");
});

