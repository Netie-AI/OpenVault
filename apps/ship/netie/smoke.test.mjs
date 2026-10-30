// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// Modified by Netie AI, 2026: the stub OpenVault enforces X-OpenVault-Admin,
// and the keyvault cases cover the admin token, openvault_forbidden and
// revoked keys.
// Modified by Netie AI, 2026: the stub key's custody is "pooled" (OpenVault's
// KeyCustody is "pooled" | "tenant"; "openvault" was never a real value), and
// new keyvault cases cover custody, the custom-only label fallback, the 5s
// request timeout, ~ expansion and expiry of the in-memory secret cache.
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
//   5. The keyvault client sends the OpenVault admin token (X-OpenVault-Admin,
//      read from OPENVAULT_ADMIN_TOKEN_PATH) and maps a missing token file,
//      a rejected token and a 403 openvault_forbidden to named 503 errors.
//
// The keyvault cases (4 and 5) need no build. Run only them with:
//   node --import tsx --test --test-name-pattern=keyvault netie/smoke.test.mjs
//
// A stub HTTP server stands in for OpenVault for the whole run (`OPENVAULT_URL`
// points at it) — the real OpenVault is never required to run this test.

import { test, describe, before, after, mock } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { randomBytes } from "node:crypto";
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
  custody: "pooled",
};
const STUB_SECRET = "stub-cloudflare-token-value";

/** Random per run, written to a mode-0600 temp file like OpenVault's own. */
const ADMIN_TOKEN = randomBytes(32).toString("hex");

let revealCalls = 0;
/** What GET /api/keys returns. Cases may swap it and must restore it. */
let stubKeys = [STUB_KEY];
/** When set, every keys route answers this 403 body (the guard or a sealed vault). */
let stubForce403 = null;
/** When true, every keys route accepts the request and never answers (a hung OpenVault). */
let stubHang = false;

function startStubOpenVault() {
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    // Real OpenVault (#84) guards every /api/keys route with the admin token,
    // loopback included.
    if (url.pathname === "/api/keys" || url.pathname.startsWith("/api/keys/")) {
      if (req.headers["x-openvault-admin"] !== ADMIN_TOKEN) {
        res.writeHead(401, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: { message: "unauthorized", type: "openvault_unauthenticated" } }));
        return;
      }
      if (stubHang) return;
      if (stubForce403) {
        res.writeHead(403, { "content-type": "application/json" });
        res.end(JSON.stringify(stubForce403));
        return;
      }
    }
    if (req.method === "GET" && url.pathname === "/api/keys") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ keys: stubKeys }));
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
  // Unref'd so the stub alone never keeps the process alive. Node's test
  // runner runs root-level after() hooks only once the event loop drains.
  server.unref();
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
let adminTokenPath;

// Stub OpenVault and the token file serve every test. The API process is
// started only by the "API process" suite below, so the keyvault cases run
// without a build.
before(async () => {
  stubOpenVault = await startStubOpenVault();
  openVaultUrl = `http://127.0.0.1:${stubOpenVault.address().port}`;

  dataDir = await mkdtemp(join(tmpdir(), "freebuild-smoke-"));
  adminTokenPath = join(dataDir, "admin_token");
  await writeFile(adminTokenPath, ADMIN_TOKEN, { mode: 0o600 });
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
});

after(async () => {
  stubOpenVault?.closeAllConnections();
  stubOpenVault?.close();
  if (dataDir) await rm(dataDir, { recursive: true, force: true }).catch(() => {});
});

// ─── API smoke tests ───────────────────────────────────────────────────────

describe("API process", () => {
before(async () => {
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
        OPENVAULT_ADMIN_TOKEN_PATH: adminTokenPath,
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
});

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

test("keyvault client sends the admin token: right token returns the secret", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
  keyvault._resetSecretCacheForTests();
  assert.equal(await keyvault.getSecret(STUB_KEY.id), STUB_SECRET);
  assert.equal((await keyvault.listKeys()).length, 1);
});

test("keyvault client: missing token file is openvault_admin_token_unavailable", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  const missing = join(dataDir, "no_such_admin_token");
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = missing;
  keyvault._resetSecretCacheForTests();
  try {
    for (const call of [() => keyvault.listKeys(), () => keyvault.getSecret(STUB_KEY.id)]) {
      await assert.rejects(call, (err) => {
        assert.equal(err.code, "openvault_admin_token_unavailable");
        assert.equal(err.statusCode, 503);
        assert.ok(err.message.includes(missing), "the message names the path");
        return true;
      });
    }
  } finally {
    process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
  }
});

test("keyvault client: wrong token is openvault_admin_token_rejected and never leaks", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  const wrongPath = join(dataDir, "wrong_admin_token");
  const wrongToken = randomBytes(32).toString("hex");
  await writeFile(wrongPath, wrongToken, { mode: 0o600 });
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = wrongPath;
  keyvault._resetSecretCacheForTests();
  try {
    for (const call of [() => keyvault.listKeys(), () => keyvault.getSecret(STUB_KEY.id)]) {
      await assert.rejects(call, (err) => {
        assert.equal(err.code, "openvault_admin_token_rejected");
        assert.equal(err.statusCode, 503);
        assert.ok(!err.message.includes(wrongToken), "the token must not appear in the error");
        assert.ok(!(err.stack ?? "").includes(wrongToken), "the token must not appear in the stack");
        return true;
      });
    }
  } finally {
    process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
  }
});

test("keyvault client: 403 openvault_forbidden is openvault_forbidden, not sealed", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
  keyvault._resetSecretCacheForTests();
  stubForce403 = { error: { message: "forbidden", type: "openvault_forbidden" } };
  try {
    for (const call of [() => keyvault.listKeys(), () => keyvault.getSecret(STUB_KEY.id)]) {
      await assert.rejects(call, (err) => {
        assert.equal(err.code, "openvault_forbidden");
        assert.equal(err.statusCode, 503);
        assert.notEqual(err.name, "OpenVaultSealedError");
        return true;
      });
    }
  } finally {
    stubForce403 = null;
  }
});

test("keyvault client: a reveal 403 without openvault_forbidden still means sealed", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
  keyvault._resetSecretCacheForTests();
  stubForce403 = { detail: "vault is sealed" };
  try {
    await assert.rejects(
      () => keyvault.getSecret(STUB_KEY.id),
      (err) => {
        assert.equal(err.code, "openvault_keyvault_sealed");
        assert.equal(err.statusCode, 503);
        return true;
      },
    );
  } finally {
    stubForce403 = null;
  }
});

test("keyvault client: findKeysForFreeBuildProvider skips revoked and replaced keys", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
  const revoked = { ...STUB_KEY, id: "key_stub_cloudflare_revoked", priority: 0, lifecycle: "revoked" };
  const replaced = { ...STUB_KEY, id: "key_stub_cloudflare_replaced", lifecycle: "replaced" };
  const noLifecycle = { ...STUB_KEY, id: "key_stub_cloudflare_legacy" };
  delete noLifecycle.lifecycle;
  stubKeys = [revoked, replaced, STUB_KEY, noLifecycle];
  try {
    const ids = (await keyvault.findKeysForFreeBuildProvider("cloudflare")).map((k) => k.id);
    assert.deepEqual(ids, [STUB_KEY.id, noLifecycle.id]);
    assert.equal((await keyvault.findKeyForFreeBuildProvider("cloudflare")).id, STUB_KEY.id);

    stubKeys = [revoked];
    assert.deepEqual(await keyvault.findKeysForFreeBuildProvider("cloudflare"), []);
  } finally {
    stubKeys = [STUB_KEY];
  }
});

test("keyvault client: spends only pooled keys or keys with no custody field", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
  const tenant = { ...STUB_KEY, id: "key_stub_cloudflare_tenant", priority: 0, custody: "tenant" };
  const other = { ...STUB_KEY, id: "key_stub_cloudflare_other", custody: "operator" };
  const noCustody = { ...STUB_KEY, id: "key_stub_cloudflare_untagged" };
  delete noCustody.custody;
  stubKeys = [tenant, other, STUB_KEY, noCustody];
  try {
    assert.equal(keyvault.isSpendableCustody("pooled"), true);
    assert.equal(keyvault.isSpendableCustody(undefined), true);
    assert.equal(keyvault.isSpendableCustody(null), true);
    assert.equal(keyvault.isSpendableCustody("tenant"), false);
    const ids = (await keyvault.findKeysForFreeBuildProvider("cloudflare")).map((k) => k.id);
    assert.deepEqual(ids, [STUB_KEY.id, noCustody.id]);

    stubKeys = [tenant];
    assert.deepEqual(await keyvault.findKeysForFreeBuildProvider("cloudflare"), []);
    // The provider-id match obeys custody too, not only the label fallback.
    stubKeys = [{ ...tenant, provider: "cloudflare" }];
    assert.deepEqual(await keyvault.findKeysForFreeBuildProvider("cloudflare"), []);
  } finally {
    stubKeys = [STUB_KEY];
  }
});

test("keyvault client: the label fallback never picks another provider's key", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
  const gateway = {
    ...STUB_KEY,
    id: "key_stub_openai_gateway",
    provider: "openai",
    label: "OpenAI via Cloudflare AI Gateway",
  };
  const midLabel = { ...STUB_KEY, id: "key_stub_custom_mid", label: "My Cloudflare token" };
  try {
    stubKeys = [gateway, midLabel];
    assert.deepEqual(await keyvault.findKeysForFreeBuildProvider("cloudflare"), []);
    assert.equal(await keyvault.findKeyForFreeBuildProvider("cloudflare"), undefined);

    stubKeys = [gateway, STUB_KEY];
    const ids = (await keyvault.findKeysForFreeBuildProvider("cloudflare")).map((k) => k.id);
    assert.deepEqual(ids, [STUB_KEY.id]);

    // A key whose provider id is "cloudflare" still wins over the label fallback.
    const byProvider = { ...STUB_KEY, id: "key_stub_cf_provider", provider: "Cloudflare", label: "zone A" };
    stubKeys = [STUB_KEY, byProvider];
    const preferred = (await keyvault.findKeysForFreeBuildProvider("cloudflare")).map((k) => k.id);
    assert.deepEqual(preferred, [byProvider.id]);
  } finally {
    stubKeys = [STUB_KEY];
  }
});

test("keyvault client: a hung OpenVault fails with the named 503 after the timeout", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
  keyvault._resetSecretCacheForTests();
  stubHang = true;
  const started = Date.now();
  try {
    await assert.rejects(
      () => keyvault.listKeys(),
      (err) => {
        assert.equal(err.code, "openvault_keyvault_unreachable");
        assert.equal(err.statusCode, 503);
        return true;
      },
    );
  } finally {
    stubHang = false;
    stubOpenVault.closeAllConnections();
  }
  const elapsed = Date.now() - started;
  assert.ok(elapsed >= 4_500 && elapsed < 15_000, `timed out after ${elapsed}ms, expected about 5000ms`);
});

test("keyvault client: expands ~ in OPENVAULT_ADMIN_TOKEN_PATH and OPENVAULT_HOME", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  const saved = {
    HOME: process.env.HOME,
    USERPROFILE: process.env.USERPROFILE,
    OPENVAULT_HOME: process.env.OPENVAULT_HOME,
  };
  process.env.HOME = dataDir;
  process.env.USERPROFILE = dataDir;
  keyvault._resetSecretCacheForTests();
  try {
    process.env.OPENVAULT_ADMIN_TOKEN_PATH = "~/admin_token";
    assert.equal(keyvault.adminTokenPath(), adminTokenPath);
    assert.equal((await keyvault.listKeys()).length, 1);

    delete process.env.OPENVAULT_ADMIN_TOKEN_PATH;
    process.env.OPENVAULT_HOME = "~";
    assert.equal(keyvault.adminTokenPath(), adminTokenPath);
    assert.equal(await keyvault.getSecret(STUB_KEY.id), STUB_SECRET);
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
    process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
  }
});

test("keyvault client: an expired secret is deleted from the in-memory cache", async () => {
  process.env.OPENVAULT_URL = openVaultUrl;
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = adminTokenPath;
  keyvault._resetSecretCacheForTests();
  const realNow = Date.now.bind(Date);
  let offset = 0;
  const clock = mock.method(Date, "now", () => realNow() + offset);
  try {
    assert.equal(await keyvault.getSecret(STUB_KEY.id), STUB_SECRET);
    assert.equal(keyvault._secretCacheSizeForTests(), 1);

    offset = 61_000; // past the 60s TTL
    // Any later lookup sweeps the cache first, even one for a different key that fails.
    await assert.rejects(() => keyvault.getSecret("key_unknown"), (err) => err.code === "openvault_key_not_found");
    assert.equal(keyvault._secretCacheSizeForTests(), 0, "the expired plaintext must be gone");

    const before = revealCalls;
    assert.equal(await keyvault.getSecret(STUB_KEY.id), STUB_SECRET);
    assert.equal(revealCalls, before + 1, "an expired entry is fetched again, not served");
  } finally {
    clock.mock.restore();
    keyvault._resetSecretCacheForTests();
  }
});
