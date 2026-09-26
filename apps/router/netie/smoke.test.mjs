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
// Run: npm run build:backend && npm run test:smoke

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import { setTimeout as sleep } from "node:timers/promises";

const ROOT = new URL("..", import.meta.url).pathname;

const MANAGEMENT_API_KEY = `sk-smoke-mgmt-${randomBytes(16).toString("hex")}`;
const OPENVAULT_VALID_TOKEN = `ovtok_smoke_${randomBytes(16).toString("hex")}`;
const OPENVAULT_BOGUS_TOKEN = `ovtok_bogus_${randomBytes(16).toString("hex")}`;
const OPENVAULT_KEY_ID = "key_smoke_test";
const OPENVAULT_VERIFY_KEY_ID = "apikey_smoke_test";

let stubServer;
let stubPort;
let serverProcess;
let appPort;
let dataDir;
const serverLogs = [];
let verifyCallCount = 0;

/** Minimal OpenVault stub: /api/keys, /api/keys/{id}/secret, /api/apikeys/verify. */
function startOpenVaultStub() {
  return new Promise((resolve, reject) => {
    const server = createServer((req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      res.setHeader("content-type", "application/json");

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
                custody: "openvault",
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

  serverProcess = spawn(process.execPath, ["scripts/dev/run-next.mjs", "start"], {
    cwd: ROOT,
    env: {
      ...process.env,
      PORT: String(appPort),
      DASHBOARD_PORT: String(appPort),
      API_PORT: String(appPort),
      OMNIROUTE_HOSTNAME: "127.0.0.1",
      HOSTNAME: "127.0.0.1",
      DATA_DIR: dataDir,
      OPENVAULT_URL: `http://127.0.0.1:${stubPort}`,
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
  if (dataDir) {
    try {
      rmSync(dataDir, { recursive: true, force: true });
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
