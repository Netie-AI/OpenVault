// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// FreeBuild smoke test (node:test, plain Node — no test framework dependency).
// Run with `npm run test:smoke` from the repo root.
//
// What this proves, end to end, against the REAL built API (apps/api/dist,
// produced by `npm run build`):
//   1. The API boots on Node 22 with an embedded PGlite database (no Postgres,
//      no Docker) and answers its health endpoint.
//   2. A hard-disabled Openship Cloud (hosted SaaS) route answers HTTP 501
//      `{"error":{"code":"hosted_cloud_disabled",...}}` instead of silently
//      failing or reaching the upstream vendor.
//   3. A route that would save a provider API key into FreeBuild's own DB
//      (here: a Cloudflare credential) answers HTTP 501
//      `{"error":{"code":"keys_managed_by_openvault",...}}` instead of storing
//      it — and does NOT call the stub OpenVault server to do so (nothing to
//      save there in the first place).
//   4. The keyvault client (packages/core/src/netie/keyvault.ts) reads a key
//      list and a secret from a stub OpenVault server, matching a FreeBuild
//      provider by label, per the exact wire contract this fork implements.
//
// A stub HTTP server stands in for OpenVault for the whole run (`OPENVAULT_URL`
// points at it) — the real OpenVault is never required to run this test.

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = dirname(HERE);
const API_DIR = join(REPO_ROOT, "apps/api");

// ─── Stub OpenVault server ────────────────────────────────────────────────

/** One fake key: a "custom" provider labeled so findKeyForFreeBuildProvider
 *  ("cloudflare") matches it by label, the same way a real operator-labeled
 *  OpenVault entry would. */
const STUB_KEY = {
  id: "key_stub_cloudflare",
  label: "Cloudflare API token",
  provider: "custom",
  role: "default",
  base_url: null,
  masked_secret: "••••••••abcd",
  enabled: true,
  priority: 1,
  precheck_status: "ok",
  account_id: "acct_stub",
  lifecycle: "active",
  custody: "openvault",
};
const STUB_SECRET = "stub-cloudflare-token-value";

let revealCalls = 0;

function startStubOpenVault() {
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    if (req.method === "GET" && url.pathname === "/api/keys") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ keys: [STUB_KEY] }));
      return;
    }
    const secretMatch = url.pathname.match(/^\/api\/keys\/([^/]+)\/secret$/);
    if (req.method === "GET" && secretMatch) {
      revealCalls++;
      if (req.headers["x-openvault-reveal"] !== "intentional") {
        res.writeHead(400, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: "missing X-OpenVault-Reveal header" }));
        return;
      }
      const id = decodeURIComponent(secretMatch[1]);
      if (id !== STUB_KEY.id) {
        res.writeHead(404, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: "unknown key" }));
        return;
      }
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ id, secret: STUB_SECRET }));
      return;
    }
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "not found" }));
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

// ─── Helpers ───────────────────────────────────────────────────────────────

async function waitForHealth(baseUrl, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  let lastErr;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseUrl}/api/health`);
      if (res.ok) return res;
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`API never became healthy at ${baseUrl}: ${lastErr}`);
}

// ─── Fixtures (shared across tests in this file) ───────────────────────────

let stubOpenVault;
let openVaultUrl;
let apiProcess;
let apiBaseUrl;
let dataDir;

before(async () => {
  stubOpenVault = await startStubOpenVault();
  openVaultUrl = `http://127.0.0.1:${stubOpenVault.address().port}`;

  dataDir = await mkdtemp(join(tmpdir(), "freebuild-smoke-"));
  const apiPort = 30000 + Math.floor(Math.random() * 10000);
  apiBaseUrl = `http://127.0.0.1:${apiPort}`;

  apiProcess = spawn(
    process.execPath,
    ["--import", "tsx", "dist/index.js"],
    {
      cwd: API_DIR,
      env: {
        ...process.env,
        NODE_ENV: "production",
        PORT: String(apiPort),
        OPENSHIP_API_HOST: "127.0.0.1",
        INTERNAL_TOKEN: "smoke-test-internal-token",
        PGLITE_DATA_DIR: join(dataDir, "pglite"),
        OPENVAULT_URL: openVaultUrl,
        // Disposable, isolated smoke-test instance only — lets an
        // unauthenticated loopback request act as admin so this script can
        // exercise an admin-gated route (POST /api/credentials) without
        // standing up a full login flow. Never appropriate for a real
        // deployment (env.ts defaults this to false for that reason).
        OPENSHIP_ALLOW_ZERO_AUTH: "true",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let apiLog = "";
  apiProcess.stdout.on("data", (d) => (apiLog += d));
  apiProcess.stderr.on("data", (d) => (apiLog += d));
  apiProcess.on("exit", (code, signal) => {
    if (code !== null && code !== 0) {
      console.error(`[smoke] API process exited early (code=${code} signal=${signal}):\n${apiLog}`);
    }
  });

  await waitForHealth(apiBaseUrl);
});

after(async () => {
  apiProcess?.kill("SIGTERM");
  stubOpenVault?.close();
  if (dataDir) await rm(dataDir, { recursive: true, force: true }).catch(() => {});
});

// ─── API smoke tests ───────────────────────────────────────────────────────

test("health endpoint answers 200", async () => {
  const res = await fetch(`${apiBaseUrl}/api/health`);
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.status, "ok");
  assert.equal(body.cloudMode, false, "FreeBuild must never boot in CLOUD_MODE");
});

test("a hosted-cloud (SaaS) route answers 501 hosted_cloud_disabled", async () => {
  const res = await fetch(`${apiBaseUrl}/api/cloud/status`);
  assert.equal(res.status, 501);
  const body = await res.json();
  assert.equal(body.error.code, "hosted_cloud_disabled");
  assert.match(body.error.message, /self-hosted/i);
});

test("saving a provider key (Cloudflare) answers 501 keys_managed_by_openvault", async () => {
  const res = await fetch(`${apiBaseUrl}/api/credentials`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      provider: "cloudflare",
      name: "smoke-test",
      values: { apiToken: "should-never-be-stored" },
    }),
  });
  assert.equal(res.status, 501);
  const body = await res.json();
  assert.equal(body.error.code, "keys_managed_by_openvault");
  assert.match(body.error.message, /OpenVault/);
  assert.match(body.error.message, /127\.0\.0\.1:3010\/keys/);
});

// ─── keyvault.ts unit test (fast, no API process — imports the module directly) ──
//
// `OPENVAULT_URL` is read fresh on every call (never cached at import time —
// see keyvault.ts's `baseUrl()`), so these tests just flip the env var between
// cases rather than re-importing the module.

const keyvault = await import("../packages/core/src/netie/keyvault.ts");

test("keyvault client reads the key list and secret from the stub OpenVault", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  const { listKeys, findKeyForFreeBuildProvider, getSecret, _resetSecretCacheForTests } = keyvault;
  _resetSecretCacheForTests();

  const keys = await listKeys();
  assert.equal(keys.length, 1);
  assert.equal(keys[0].id, STUB_KEY.id);

  const match = await findKeyForFreeBuildProvider("cloudflare");
  assert.ok(match, "expected a label match for the 'cloudflare' FreeBuild provider");
  assert.equal(match.id, STUB_KEY.id);

  const before = revealCalls;
  const secret = await getSecret(match.id);
  assert.equal(secret, STUB_SECRET);
  assert.equal(revealCalls, before + 1);

  // Cached in memory: a second call within the TTL must not hit the stub again.
  const secretAgain = await getSecret(match.id);
  assert.equal(secretAgain, STUB_SECRET);
  assert.equal(revealCalls, before + 1, "expected the in-memory cache to skip a second network call");
});

test("keyvault client fails loud (never a silent fallback) when OpenVault is unreachable", async () => {
  process.env.OPENVAULT_URL = "http://127.0.0.1:1"; // nothing listens here
  await assert.rejects(
    () => keyvault.listKeys(),
    (err) => {
      assert.equal(err.code, "openvault_keyvault_unreachable");
      assert.equal(err.statusCode, 503);
      return true;
    },
  );
  process.env.OPENVAULT_URL = openVaultUrl;
});
