// Copyright (c) 2026 Netie AI. MIT.
//
// Fast unit test for src/lib/netie/keyvault.ts, runnable without a build.
// A stub HTTP server enforces X-OpenVault-Admin against a token in a temp
// file, the same way OpenVault does (401 openvault_unauthenticated when the
// header is missing or wrong, loopback included).
//
// Run: node --import tsx --test netie/keyvault.test.mjs
// (wired as `npm run test:keyvault`, part of `npm test`.)

import { test, before, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtempSync, rmSync, writeFileSync, unlinkSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

import {
  resolveProviderApiKey,
  verifyClientToken,
  resolveAdminTokenPath,
  isSpendableCustody,
  OpenVaultUnreachableError,
  __resetKeyVaultCacheForTest,
} from "../src/lib/netie/keyvault.ts";
import {
  assertConnectionWriteAllowed,
  findSecretProviderSpecificFields,
  netieErrorResponse,
  rejectProviderSecretsInBody,
  KeysManagedByOpenVaultError,
} from "../src/lib/netie/providerGuards.ts";

const ADMIN_TOKEN = `adm_${randomBytes(24).toString("hex")}`;
const KEY_ID = "key_unit_test";
const SECRET = "sk-unit-test-not-real";
const ENDPOINT_KEY_ID = "key_unit_endpoint";
const ENDPOINT_SECRET = "sk-unit-endpoint-not-real";
const ENDPOINT_BASE_URL = "http://127.0.0.1:9/v1";
const VALID_CLIENT_TOKEN = `ovtok_unit_${randomBytes(8).toString("hex")}`;
// Custody fixtures. OpenVault's own router spends only custody "pooled" keys
// (OpenMW/openmw/openvault/vault/fallback.py _is_available, store.py
// pooled_ordered); a row with no custody field predates the tag.
const TENANT_KEY_ID = "key_unit_tenant";
const TENANT_SECRET = "sk-unit-tenant-must-never-be-spent";
const POOLED_ANTHROPIC_KEY_ID = "key_unit_anthropic_pooled";
const POOLED_ANTHROPIC_SECRET = "sk-unit-anthropic-pooled";
const MISTRAL_TENANT_ONLY_ID = "key_unit_mistral_tenant";
const COHERE_UNKNOWN_CUSTODY_ID = "key_unit_cohere_unknown";
const CUSTOM_OTHER_ENDPOINT_ID = "key_unit_custom_other";
const CUSTOM_OTHER_ENDPOINT_SECRET = "sk-unit-custom-other-endpoint";
const EXACT_ID_KEY_ID = "key_unit_llm7_exact";
const EXACT_ID_SECRET = "sk-unit-llm7-exact";

let tmpDir;
let tokenPath;
let server;
let baseUrl;
let sealed = false;
const savedEnv = {};

function startStub() {
  return new Promise((resolve, reject) => {
    const srv = createServer((req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      res.setHeader("content-type", "application/json");

      if (req.headers["x-openvault-admin"] !== ADMIN_TOKEN) {
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
                id: KEY_ID,
                provider: "openai",
                enabled: true,
                priority: 10,
                lifecycle: "active",
              },
              {
                id: ENDPOINT_KEY_ID,
                provider: "groq",
                base_url: ENDPOINT_BASE_URL,
                enabled: true,
                priority: 5,
                lifecycle: "active",
              },
              // Higher priority than the pooled anthropic key, but a tenant's.
              {
                id: TENANT_KEY_ID,
                provider: "anthropic",
                enabled: true,
                priority: 100,
                lifecycle: "active",
                custody: "tenant",
              },
              {
                id: POOLED_ANTHROPIC_KEY_ID,
                provider: "anthropic",
                enabled: true,
                priority: 1,
                lifecycle: "active",
                custody: "pooled",
              },
              {
                id: MISTRAL_TENANT_ONLY_ID,
                provider: "mistral",
                enabled: true,
                priority: 10,
                lifecycle: "active",
                custody: "tenant",
              },
              {
                id: COHERE_UNKNOWN_CUSTODY_ID,
                provider: "cohere",
                enabled: true,
                priority: 10,
                lifecycle: "active",
                custody: "borrowed",
              },
              {
                id: CUSTOM_OTHER_ENDPOINT_ID,
                provider: "custom",
                base_url: "http://127.0.0.1:11/v1",
                enabled: true,
                priority: 50,
                lifecycle: "active",
                custody: "pooled",
              },
              {
                id: EXACT_ID_KEY_ID,
                provider: "llm7",
                enabled: true,
                priority: 1,
                lifecycle: "active",
                custody: "pooled",
              },
            ],
          })
        );
        return;
      }

      const secretMatch = /^\/api\/keys\/([^/]+)\/secret$/.exec(url.pathname);
      const secrets = {
        [KEY_ID]: SECRET,
        [ENDPOINT_KEY_ID]: ENDPOINT_SECRET,
        [TENANT_KEY_ID]: TENANT_SECRET,
        [POOLED_ANTHROPIC_KEY_ID]: POOLED_ANTHROPIC_SECRET,
        [MISTRAL_TENANT_ONLY_ID]: TENANT_SECRET,
        [COHERE_UNKNOWN_CUSTODY_ID]: TENANT_SECRET,
        [CUSTOM_OTHER_ENDPOINT_ID]: CUSTOM_OTHER_ENDPOINT_SECRET,
        [EXACT_ID_KEY_ID]: EXACT_ID_SECRET,
      };
      if (secretMatch && secretMatch[1] in secrets && req.method === "GET") {
        if (req.headers["x-openvault-reveal"] !== "intentional") {
          res.writeHead(400);
          res.end(JSON.stringify({ detail: "reveal intent header required" }));
          return;
        }
        if (sealed) {
          res.writeHead(403);
          res.end(
            JSON.stringify({
              detail: "vault is sealed; POST /api/vault/unseal with the passphrase first",
            })
          );
          return;
        }
        res.writeHead(200);
        res.end(JSON.stringify({ id: secretMatch[1], secret: secrets[secretMatch[1]] }));
        return;
      }

      if (url.pathname === "/api/apikeys/verify" && req.method === "POST") {
        let body = "";
        req.on("data", (chunk) => (body += chunk));
        req.on("end", () => {
          const token = JSON.parse(body || "{}").token;
          res.writeHead(200);
          res.end(
            JSON.stringify(
              token === VALID_CLIENT_TOKEN
                ? { valid: true, key_id: "apikey_unit", tier: "free" }
                : { valid: false }
            )
          );
        });
        return;
      }

      res.writeHead(404);
      res.end(JSON.stringify({ detail: "not found in stub" }));
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
    srv.on("error", reject);
  });
}

/** A loopback URL with nothing listening on it. */
function getDeadUrl() {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(`http://127.0.0.1:${port}`));
    });
    srv.on("error", reject);
  });
}

before(async () => {
  for (const name of ["OPENVAULT_URL", "OPENVAULT_ADMIN_TOKEN_PATH", "OPENVAULT_HOME"]) {
    savedEnv[name] = process.env[name];
  }
  tmpDir = mkdtempSync(join(tmpdir(), "freeroute-keyvault-"));
  tokenPath = join(tmpDir, "admin_token");
  server = await startStub();
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  for (const [name, value] of Object.entries(savedEnv)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
  if (server) await new Promise((resolve) => server.close(resolve));
  if (tmpDir) rmSync(tmpDir, { recursive: true, force: true });
});

beforeEach(() => {
  __resetKeyVaultCacheForTest();
  sealed = false;
  writeFileSync(tokenPath, `${ADMIN_TOKEN}\n`, { mode: 0o600 });
  process.env.OPENVAULT_URL = baseUrl;
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = tokenPath;
  delete process.env.OPENVAULT_HOME;
});

/** Assert a rejection is a 503 KeyVault error with `code`, and never leaks the token. */
async function assertKeyVaultError(promise, code) {
  await assert.rejects(promise, (error) => {
    assert.ok(
      error instanceof OpenVaultUnreachableError,
      `expected OpenVaultUnreachableError, got ${error}`
    );
    assert.equal(error.status, 503);
    assert.equal(error.code, code);
    assert.ok(
      !error.message.includes(ADMIN_TOKEN),
      "error message must not contain the admin token"
    );
    return true;
  });
}

test("admin token path: OPENVAULT_ADMIN_TOKEN_PATH, then OPENVAULT_HOME, then ~/.openvault", () => {
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = "/x/explicit_token";
  process.env.OPENVAULT_HOME = "/x/home";
  assert.equal(resolveAdminTokenPath(), "/x/explicit_token");
  delete process.env.OPENVAULT_ADMIN_TOKEN_PATH;
  assert.equal(resolveAdminTokenPath(), join("/x/home", "admin_token"));
  delete process.env.OPENVAULT_HOME;
  assert.match(resolveAdminTokenPath(), /[\\/]\.openvault[\\/]admin_token$/);
});

test("right token: the provider secret is returned", async () => {
  assert.equal(await resolveProviderApiKey("openai"), SECRET);
});

test("an OpenAI-compatible node finds the key stored for its exact base URL", async () => {
  // Trailing slash and host case do not matter; the router id cannot be mapped.
  assert.equal(
    await resolveProviderApiKey("openai-compatible-chat-abc", {
      baseUrl: "http://127.0.0.1:9/V1/",
    }),
    ENDPOINT_SECRET
  );
});

test("a base URL match wins over the provider-id map", async () => {
  assert.equal(
    await resolveProviderApiKey("openai", { baseUrl: ENDPOINT_BASE_URL }),
    ENDPOINT_SECRET
  );
});

test("no base URL match: falls back to the provider-id map, else null", async () => {
  assert.equal(
    await resolveProviderApiKey("openai", { baseUrl: "https://api.openai.com/v1" }),
    SECRET
  );
  assert.equal(
    await resolveProviderApiKey("openai-compatible-chat-abc", {
      baseUrl: "http://127.0.0.1:10/v1",
    }),
    null
  );
});

test("custody: only pooled or untagged keys are spendable", () => {
  assert.equal(isSpendableCustody(undefined), true);
  assert.equal(isSpendableCustody(null), true);
  assert.equal(isSpendableCustody("pooled"), true);
  assert.equal(isSpendableCustody("tenant"), false);
  assert.equal(isSpendableCustody("borrowed"), false);
  assert.equal(isSpendableCustody(""), false);
});

test("custody: a tenant key is never picked, even at a higher priority", async () => {
  assert.equal(await resolveProviderApiKey("anthropic"), POOLED_ANTHROPIC_SECRET);
});

test("custody: a provider with only a tenant key resolves to null", async () => {
  assert.equal(await resolveProviderApiKey("mistral"), null);
});

test("custody: an unknown custody value is not spendable", async () => {
  assert.equal(await resolveProviderApiKey("cohere"), null);
});

test("an unmapped provider never borrows another endpoint's custom key", async () => {
  // Before: an unmapped id fell back to "any custom key", here the one stored
  // for http://127.0.0.1:11/v1, and sent it to a different provider.
  assert.equal(await resolveProviderApiKey("some-unmapped-provider"), null);
  assert.equal(
    await resolveProviderApiKey("openai-compatible-chat-xyz", { baseUrl: "http://127.0.0.1:12/v1" }),
    null
  );
  // A key filed under the exact router id is still found.
  assert.equal(await resolveProviderApiKey("llm7"), EXACT_ID_SECRET);
  // And the endpoint's own connection still finds its key by base URL.
  assert.equal(
    await resolveProviderApiKey("openai-compatible-chat-xyz", { baseUrl: "http://127.0.0.1:11/v1" }),
    CUSTOM_OTHER_ENDPOINT_SECRET
  );
});

// ── providerGuards: the DB-layer write guard and the route-level checks ─────

test("write guard: a provider apiKey is refused with keys_managed_by_openvault", () => {
  assert.throws(
    () => assertConnectionWriteAllowed({ provider: "openai", authType: "apikey", apiKey: "sk-x" }),
    (error) => error instanceof KeysManagedByOpenVaultError && error.status === 501
  );
  // No key: allowed.
  assertConnectionWriteAllowed({ provider: "openai", authType: "apikey", name: "n" });
  assertConnectionWriteAllowed({ provider: "openai", authType: "apikey", apiKey: "" });
});

test("write guard: extraApiKeys and other secret providerSpecificData fields are refused", () => {
  for (const psd of [
    { extraApiKeys: ["sk-extra"] },
    { consoleApiKey: "ck" },
    { cookie: "a=b" },
    { awsSessionToken: "t" },
    { pat: "p" },
  ]) {
    assert.throws(
      () =>
        assertConnectionWriteAllowed({
          provider: "openai",
          authType: "apikey",
          providerSpecificData: psd,
        }),
      KeysManagedByOpenVaultError,
      JSON.stringify(psd)
    );
  }
  // Metadata that only looks secret-adjacent stays allowed.
  assert.deepEqual(
    findSecretProviderSpecificFields({
      baseUrl: "https://x",
      apiKeyHealth: { a: 1 },
      tokenExpiresAt: "2026",
      accessKeyId: "AKIA",
      tokenEndpoint: "https://t",
      extraApiKeys: [],
    }),
    []
  );
});

test("write guard: a value carried over unchanged is not a new write", () => {
  const existing = {
    provider: "openai",
    authType: "apikey",
    apiKey: "sk-legacy",
    providerSpecificData: { extraApiKeys: ["sk-old"] },
  };
  assertConnectionWriteAllowed({ ...existing, testStatus: "active" }, existing);
  assert.throws(
    () =>
      assertConnectionWriteAllowed(
        { providerSpecificData: { extraApiKeys: ["sk-new"] } },
        existing
      ),
    KeysManagedByOpenVaultError
  );
});

test("write guard: classified providers cannot be created, or get new credentials", () => {
  assert.throws(
    () => assertConnectionWriteAllowed({ provider: "trae", authType: "oauth", accessToken: "t" }),
    (error) => error.status === 501 && error.code === "consumer_subscription_pooling_disabled"
  );
  assert.throws(
    () => assertConnectionWriteAllowed({ provider: "copilot-web", authType: "apikey", name: "x" }),
    (error) => error.code === "consumer_session_relay_disabled"
  );
  const restored = { provider: "codex", authType: "oauth", accessToken: "old" };
  // Status writes on a restored row stay allowed so it can be deactivated.
  assertConnectionWriteAllowed({ isActive: false }, restored);
  assert.throws(
    () => assertConnectionWriteAllowed({ accessToken: "refreshed" }, restored),
    (error) => error.code === "consumer_subscription_pooling_disabled"
  );
});

test("bulk/import body check: named 501 for keys and for classified providers", async () => {
  const keyed = rejectProviderSecretsInBody({
    provider: "openai",
    entries: [{ name: "a", apiKey: "sk-a" }],
  });
  assert.equal(keyed.status, 501);
  assert.equal((await keyed.json()).error.code, "keys_managed_by_openvault");

  const classified = rejectProviderSecretsInBody({
    entries: [{ provider: "claude-web", name: "a", apiKey: "cookie" }],
  });
  assert.equal(classified.status, 501);
  assert.equal((await classified.json()).error.code, "consumer_session_relay_disabled");

  assert.equal(rejectProviderSecretsInBody({ provider: "openai", entries: [{ name: "a" }] }), null);
});

test("netieErrorResponse keeps the named code for KeyVault and key-storage errors", async () => {
  const vault = netieErrorResponse(
    new OpenVaultUnreachableError("down", "openvault_admin_token_rejected")
  );
  assert.equal(vault.status, 503);
  assert.equal((await vault.json()).error.code, "openvault_admin_token_rejected");
  const keys = netieErrorResponse(new KeysManagedByOpenVaultError(["apiKey"]));
  assert.equal(keys.status, 501);
  assert.equal((await keys.json()).error.code, "keys_managed_by_openvault");
  assert.equal(netieErrorResponse(new Error("other")), null);
});

test("missing token file: openvault_admin_token_unavailable naming the path", async () => {
  unlinkSync(tokenPath);
  assert.equal(existsSync(tokenPath), false);
  await assertKeyVaultError(resolveProviderApiKey("openai"), "openvault_admin_token_unavailable");
  await assert.rejects(resolveProviderApiKey("openai"), (error) => {
    assert.ok(error.message.includes(tokenPath), "message must name the token path");
    assert.match(error.message, /first start/);
    assert.match(error.message, /OPENVAULT_ADMIN_TOKEN_PATH/);
    return true;
  });
});

test("empty token file: openvault_admin_token_unavailable", async () => {
  writeFileSync(tokenPath, "  \n", { mode: 0o600 });
  await assertKeyVaultError(resolveProviderApiKey("openai"), "openvault_admin_token_unavailable");
});

test("wrong token: openvault_admin_token_rejected", async () => {
  writeFileSync(tokenPath, `adm_wrong_${randomBytes(8).toString("hex")}`, { mode: 0o600 });
  await assertKeyVaultError(resolveProviderApiKey("openai"), "openvault_admin_token_rejected");
});

test("OpenVault down: openvault_keyvault_unreachable", async () => {
  process.env.OPENVAULT_URL = await getDeadUrl();
  await assertKeyVaultError(resolveProviderApiKey("openai"), "openvault_keyvault_unreachable");
});

test("sealed vault on secret reveal: openvault_keyvault_sealed", async () => {
  sealed = true;
  await assertKeyVaultError(resolveProviderApiKey("openai"), "openvault_keyvault_sealed");
});

test("verifyClientToken: valid with the right admin token", async () => {
  const result = await verifyClientToken(VALID_CLIENT_TOKEN);
  assert.deepEqual(result, { valid: true, keyId: "apikey_unit", tier: "free" });
});

test("verifyClientToken: an unknown client token is invalid", async () => {
  assert.equal((await verifyClientToken("ovtok_not_issued")).valid, false);
});

test("verifyClientToken: invalid (fail closed) on a wrong admin token, warning names the code", async () => {
  writeFileSync(tokenPath, `adm_wrong_${randomBytes(8).toString("hex")}`, { mode: 0o600 });
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => warnings.push(args.join(" "));
  try {
    const result = await verifyClientToken(VALID_CLIENT_TOKEN);
    assert.equal(result.valid, false);
  } finally {
    console.warn = originalWarn;
  }
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /openvault_admin_token_rejected/);
  assert.ok(!warnings[0].includes(ADMIN_TOKEN), "warning must not contain the admin token");
});

test("verifyClientToken: invalid (fail closed) when the token file is missing", async () => {
  unlinkSync(tokenPath);
  const originalWarn = console.warn;
  const warnings = [];
  console.warn = (...args) => warnings.push(args.join(" "));
  try {
    assert.equal((await verifyClientToken(VALID_CLIENT_TOKEN)).valid, false);
  } finally {
    console.warn = originalWarn;
  }
  assert.match(warnings[0] ?? "", /openvault_admin_token_unavailable/);
});

test("verifyClientToken: invalid (fail closed) when OpenVault is down", async () => {
  process.env.OPENVAULT_URL = await getDeadUrl();
  const originalWarn = console.warn;
  const warnings = [];
  console.warn = (...args) => warnings.push(args.join(" "));
  try {
    assert.equal((await verifyClientToken(VALID_CLIENT_TOKEN)).valid, false);
  } finally {
    console.warn = originalWarn;
  }
  assert.match(warnings[0] ?? "", /openvault_keyvault_unreachable/);
});
