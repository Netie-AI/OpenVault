// Copyright (c) 2026 Netie AI. MIT.
//
// Fast unit test for src/lib/netie/keyvault.ts, runnable without a build.
// A stub HTTP server enforces X-OpenVault-Admin against a token in a temp
// file, the same way OpenVault does (401 openvault_unauthenticated when the
// header is missing or wrong, loopback included). Its /api/apikeys/verify
// answers OpenVault's exact shapes: {"ok":true,"valid":true,"key_id","tier"},
// {"ok":true,"valid":false}, and 429 {"detail":"too many verify requests"}.
//
// Also covers, without a server: the provider write guard (nested secret
// fields), the secret scrub (raw database bytes after a scrub), the cloud agent
// and semantic-cache key paths, and the CLI refusal to store a provider key.
//
// Run: node --import tsx --test netie/keyvault.test.mjs
// (wired as `npm run test:keyvault`, part of `npm test`.)

import { test, before, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import {
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
  unlinkSync,
  existsSync,
} from "node:fs";
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
  findIncomingSecrets,
  findSecretProviderSpecificFields,
  isSecretFieldName,
  netieErrorResponse,
  rejectProviderSecretsInBody,
  stripSecretProviderSpecificData,
  KeysManagedByOpenVaultError,
} from "../src/lib/netie/providerGuards.ts";
import { scrubLocalProviderSecrets } from "../src/lib/netie/scrubLocalSecrets.ts";

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
const DEVIN_KEY_ID = "key_unit_devin";
const DEVIN_SECRET = "sk-unit-devin-agent";

let tmpDir;
let tokenPath;
let server;
let baseUrl;
let sealed = false;
// What the stub's POST /api/apikeys/verify does: "ok" answers OpenVault's exact
// shapes, "rate_limited" answers 429, "legacy_shape" drops the "ok" field.
let verifyMode = "ok";
let verifyCalls = 0;
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
              {
                id: DEVIN_KEY_ID,
                provider: "devin",
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
        [DEVIN_KEY_ID]: DEVIN_SECRET,
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
        verifyCalls += 1;
        let body = "";
        req.on("data", (chunk) => (body += chunk));
        req.on("end", () => {
          // OpenVault's limiter (60 verify calls a minute per caller) answers after
          // the guard has admitted the caller, so the admin check above ran first.
          if (verifyMode === "rate_limited") {
            res.writeHead(429);
            res.end(JSON.stringify({ detail: "too many verify requests" }));
            return;
          }
          const token = JSON.parse(body || "{}").token;
          const good = token === VALID_CLIENT_TOKEN;
          res.writeHead(200);
          if (verifyMode === "legacy_shape") {
            // The shape before main added "ok": a client must not accept it.
            res.end(JSON.stringify(good ? { valid: true, key_id: "apikey_unit", tier: "free" } : { valid: false }));
            return;
          }
          res.end(
            JSON.stringify(
              good
                ? { ok: true, valid: true, key_id: "apikey_unit", tier: "free" }
                : { ok: true, valid: false }
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
  verifyMode = "ok";
  verifyCalls = 0;
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

// ── Item 17: OpenVault's verify shapes and its 429 ───────────────────────────

/** Run `fn` with console.warn captured; resolves to the captured lines. */
async function captureWarnings(fn) {
  const originalWarn = console.warn;
  const warnings = [];
  console.warn = (...args) => warnings.push(args.join(" "));
  try {
    await fn();
  } finally {
    console.warn = originalWarn;
  }
  return warnings;
}

test("verifyClientToken: the exact {ok,valid,key_id,tier} shape is accepted, {ok:true,valid:false} is invalid and cached", async () => {
  const good = await verifyClientToken(VALID_CLIENT_TOKEN);
  assert.deepEqual(good, { valid: true, keyId: "apikey_unit", tier: "free" });
  assert.equal((await verifyClientToken("ovtok_not_issued")).valid, false);
  assert.equal((await verifyClientToken("ovtok_not_issued")).valid, false);
  assert.equal(verifyCalls, 2, "the negative answer is cached for the same token");
});

test("verifyClientToken: valid:true without ok:true is not accepted (fail closed)", async () => {
  verifyMode = "legacy_shape";
  assert.equal((await verifyClientToken(VALID_CLIENT_TOKEN)).valid, false);
});

test("verifyClientToken: a 429 is invalid, logged as openvault_verify_rate_limited, and not cached", async () => {
  verifyMode = "rate_limited";
  let limited;
  const warnings = await captureWarnings(async () => {
    limited = await verifyClientToken(VALID_CLIENT_TOKEN);
  });
  assert.equal(limited.valid, false);
  assert.equal(warnings.length, 1, warnings.join("\n"));
  assert.match(warnings[0], /openvault_verify_rate_limited/);
  assert.match(warnings[0], /rate-limit/i);
  assert.doesNotMatch(warnings[0], /openvault_keyvault_unreachable/);
  assert.ok(!warnings[0].includes(ADMIN_TOKEN), "warning must not contain the admin token");

  // Not cached: once OpenVault stops limiting, the same token is valid again.
  verifyMode = "ok";
  assert.deepEqual(await verifyClientToken(VALID_CLIENT_TOKEN), {
    valid: true,
    keyId: "apikey_unit",
    tier: "free",
  });
  assert.equal(verifyCalls, 2);
});

test("verifyClientToken: a rate-limit warning and an outage warning do not mute each other", async () => {
  verifyMode = "rate_limited";
  const warnings = await captureWarnings(async () => {
    await verifyClientToken(VALID_CLIENT_TOKEN);
    process.env.OPENVAULT_URL = await getDeadUrl();
    await verifyClientToken(`${VALID_CLIENT_TOKEN}-other`);
  });
  assert.equal(warnings.length, 2, warnings.join("\n"));
  assert.match(warnings[0], /openvault_verify_rate_limited/);
  assert.match(warnings[1], /openvault_keyvault_unreachable/);
});

test("a 429 only means rate-limited on the verify route", async () => {
  // OpenVaultVerifyRateLimitedError is the verify answer; a 429 elsewhere stays the generic error.
  const { OpenVaultVerifyRateLimitedError, isOpenVaultKeyVaultError } = await import(
    "../src/lib/netie/keyvault.ts"
  );
  const error = new OpenVaultVerifyRateLimitedError("/api/apikeys/verify", 12);
  assert.equal(error.code, "openvault_verify_rate_limited");
  assert.equal(error.retryAfterSeconds, 12);
  assert.equal(isOpenVaultKeyVaultError(error), true);
  assert.ok(!error.message.includes(ADMIN_TOKEN));
});

// ── Item 9: the write guard walks nested providerSpecificData ───────────────

test("write guard: secret-looking keys are refused at any depth and by word", () => {
  for (const psd of [
    { customHeaders: { Authorization: "Bearer sk-nested" } },
    { customHeaders: { "X-Api-Key": "sk-nested" } },
    { nested: { deeper: [{ token_v2: "t" }] } },
    { auth: { credentials: { user: "u", password: "p" } } },
    { settings: { privateKey: "-----BEGIN PRIVATE KEY-----" } },
    { aws: { secretAccessKey: "wJalr" } },
    { bearer: "b" },
    { session: "s" },
    { credential: "c" },
    { private: "p" },
    { cookieJar: { sid: "x" }, token: "t" },
    // A header-named field holds the credential itself, it is not metadata.
    { authorizationHeader: "Bearer sk-nested" },
    { nested: { cookieHeader: "sid=abc" } },
    { bearerHeader: "sk-nested" },
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
});

test("write guard: metadata that only looks secret-adjacent stays allowed at any depth", () => {
  const metadata = {
    baseUrl: "https://x",
    nested: {
      tokenExpiresAt: "2026-01-01",
      apiKeyHealth: { slot0: { failures: 1 } },
      tokenEndpoint: "https://t",
      accessKeyId: "AKIA",
      codexAppServerTokenFile: "/x/auth.json",
      maxTokens: 4096,
      persistSession: true,
      ccSessionId: "abc",
      customHeaders: { "X-Org": "org-1" },
      emptyToken: undefined,
      token: "",
    },
  };
  assert.deepEqual(findSecretProviderSpecificFields(metadata), []);
  assertConnectionWriteAllowed({ provider: "openai", authType: "apikey", providerSpecificData: metadata });
});

test("secret field names: reported as dotted paths, stripped at any depth", () => {
  const psd = {
    baseUrl: "https://x",
    customHeaders: { Authorization: "Bearer a", "X-Org": "o" },
    nested: { deeper: [{ token_v2: "t", keep: 1 }] },
  };
  assert.deepEqual(findSecretProviderSpecificFields(psd), [
    "customHeaders.Authorization",
    "nested.deeper.token_v2",
  ]);
  assert.deepEqual(stripSecretProviderSpecificData(psd), {
    baseUrl: "https://x",
    customHeaders: { "X-Org": "o" },
    nested: { deeper: [{ keep: 1 }] },
  });
  for (const name of ["Authorization", "X-Api-Key", "token_v2", "bearerToken", "privateKey", "sessionCookie"]) {
    assert.equal(isSecretFieldName(name), true, name);
  }
  for (const name of ["tokenExpiresAt", "apiKeyHealth", "accessKeyId", "tokenEndpoint", "baseUrl", "sessionId"]) {
    assert.equal(isSecretFieldName(name), false, name);
  }
});

test("write guard: a nested secret carried over unchanged is not a new write, a changed one is", () => {
  const existing = {
    provider: "openai",
    authType: "apikey",
    providerSpecificData: { customHeaders: { Authorization: "Bearer old" } },
  };
  assert.deepEqual(
    findIncomingSecrets({ providerSpecificData: { customHeaders: { Authorization: "Bearer old" } } }, existing),
    []
  );
  assert.deepEqual(
    findIncomingSecrets({ providerSpecificData: { customHeaders: { Authorization: "Bearer new" } } }, existing),
    ["providerSpecificData.customHeaders.Authorization"]
  );
});

// ── Items 6, 7, 8: keys and secrets that must not stay in FreeRoute's database ──

const SENTINELS = {
  providerKey: `sk-sentinel-provider-${randomBytes(8).toString("hex")}`,
  extraKey: `sk-sentinel-extra-${randomBytes(8).toString("hex")}`,
  headerKey: `Bearer sk-sentinel-header-${randomBytes(8).toString("hex")}`,
  accessToken: `sentinel-access-${randomBytes(8).toString("hex")}`,
  refreshToken: `sentinel-refresh-${randomBytes(8).toString("hex")}`,
  embeddingDb: `sk-sentinel-embedding-db-${randomBytes(8).toString("hex")}`,
  embeddingFlat: `sk-sentinel-embedding-flat-${randomBytes(8).toString("hex")}`,
  embeddingNested: `sk-sentinel-embedding-nested-${randomBytes(8).toString("hex")}`,
  agentKey: `sk-sentinel-agent-${randomBytes(8).toString("hex")}`,
  clientKey: `ovtok-sentinel-client-${randomBytes(8).toString("hex")}`,
};

/** A SQLite file shaped like an imported FreeRoute database, holding every sentinel secret. */
async function writeImportedDatabase(path, { wal = false } = {}) {
  const { DatabaseSync } = await import("node:sqlite");
  const db = new DatabaseSync(path);
  if (wal) db.exec("PRAGMA journal_mode = WAL");
  db.exec(`
    CREATE TABLE provider_connections (
      id TEXT PRIMARY KEY, provider TEXT, auth_type TEXT, name TEXT, api_key TEXT,
      access_token TEXT, refresh_token TEXT, id_token TEXT, is_active INTEGER,
      provider_specific_data TEXT
    );
    CREATE TABLE api_keys (id TEXT PRIMARY KEY, name TEXT, key TEXT);
    CREATE TABLE key_value (namespace TEXT, key TEXT, value TEXT, PRIMARY KEY (namespace, key));
    CREATE TABLE cloud_agent_credentials (
      provider_id TEXT PRIMARY KEY, api_key_encrypted TEXT, base_url TEXT, updated_at TEXT
    );
  `);
  db.prepare(
    "INSERT INTO provider_connections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
  ).run(
    "conn-openai",
    "openai",
    "apikey",
    "main",
    SENTINELS.providerKey,
    null,
    null,
    null,
    1,
    JSON.stringify({
      baseUrl: "https://api.openai.example/v1",
      extraApiKeys: [SENTINELS.extraKey],
      customHeaders: { Authorization: SENTINELS.headerKey, "X-Org": "keep-me" },
    })
  );
  db.prepare(
    "INSERT INTO provider_connections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
  ).run("conn-codex", "codex", "oauth", "sub", null, SENTINELS.accessToken, SENTINELS.refreshToken, null, 1, null);
  db.prepare("INSERT INTO api_keys VALUES (?, ?, ?)").run("k1", "local", SENTINELS.clientKey);
  const put = db.prepare("INSERT INTO key_value VALUES (?, ?, ?)");
  put.run("databaseSettings", "cache.semanticCacheEmbeddingApiKey", JSON.stringify(SENTINELS.embeddingDb));
  put.run("settings", "semanticCacheEmbeddingApiKey", JSON.stringify(SENTINELS.embeddingFlat));
  put.run(
    "settings",
    "databaseSettings",
    JSON.stringify({ cache: { semanticCacheEmbeddingApiKey: SENTINELS.embeddingNested, semanticCacheThreshold: 0.8 } })
  );
  put.run("settings", "unrelated", JSON.stringify("keep-me-too"));
  db.prepare("INSERT INTO cloud_agent_credentials VALUES (?, ?, ?, ?)").run(
    "devin",
    SENTINELS.agentKey,
    null,
    "2026-10-01"
  );
  // Pad so the secrets sit on pages that the scrub frees rather than on the only page.
  const pad = db.prepare("INSERT INTO key_value VALUES (?, ?, ?)");
  for (let i = 0; i < 200; i += 1) pad.run("padding", `row-${i}`, "x".repeat(200));
  return db;
}

function dbFileBytes(path) {
  const parts = [path, `${path}-wal`, `${path}-journal`].filter((candidate) => existsSync(candidate));
  return Buffer.concat(parts.map((part) => readFileSync(part)));
}

function sentinelsIn(bytes) {
  return Object.entries(SENTINELS)
    .filter(([, value]) => bytes.includes(Buffer.from(value)))
    .map(([name]) => name);
}

for (const wal of [false, true]) {
  test(`scrub: no secret bytes are left in the raw database file after scrubbing an imported DB (${wal ? "WAL" : "rollback journal"})`, async () => {
    const path = join(tmpDir, `imported-${wal ? "wal" : "journal"}.sqlite`);
    const db = await writeImportedDatabase(path, { wal });
    // Sanity: the grep can find what it looks for, so an empty result below means something.
    assert.deepEqual(sentinelsIn(dbFileBytes(path)).sort(), Object.keys(SENTINELS).sort());

    const result = scrubLocalProviderSecrets(db, { dropClientKeys: true });
    db.close();

    assert.deepEqual(sentinelsIn(dbFileBytes(path)), [], "secret bytes remain in the database file");
    assert.equal(result.vacuumed, true);
    assert.equal(result.clearedApiKeys, 1);
    assert.equal(result.disabledClassified, 1);
    assert.equal(result.droppedClientKeys, 1);
    assert.equal(result.droppedAgentCredentials, 1);
    assert.equal(result.clearedEmbeddingKeys, 3);

    // What should survive does: the rows, the non-secret metadata, unrelated settings.
    const { DatabaseSync } = await import("node:sqlite");
    const after = new DatabaseSync(path);
    const conn = after.prepare("SELECT * FROM provider_connections WHERE id = 'conn-openai'").get();
    assert.equal(conn.api_key, null);
    assert.deepEqual(JSON.parse(conn.provider_specific_data), {
      baseUrl: "https://api.openai.example/v1",
      customHeaders: { "X-Org": "keep-me" },
    });
    const codex = after.prepare("SELECT * FROM provider_connections WHERE id = 'conn-codex'").get();
    assert.equal(codex.access_token, null);
    assert.equal(codex.is_active, 0);
    assert.equal(
      JSON.parse(after.prepare("SELECT value FROM key_value WHERE key = 'unrelated'").get().value),
      "keep-me-too"
    );
    assert.equal(
      JSON.parse(
        after.prepare("SELECT value FROM key_value WHERE namespace = 'settings' AND key = 'databaseSettings'").get().value
      ).cache.semanticCacheThreshold,
      0.8
    );
    assert.equal(after.prepare("SELECT COUNT(*) AS n FROM cloud_agent_credentials").get().n, 0);
    after.close();
  });
}

test("scrub: a second run over a clean database changes nothing and does not VACUUM", async () => {
  const path = join(tmpDir, "clean.sqlite");
  const db = await writeImportedDatabase(path);
  scrubLocalProviderSecrets(db, { dropClientKeys: true });
  const again = scrubLocalProviderSecrets(db, { dropClientKeys: true });
  db.close();
  assert.equal(again.vacuumed, false);
  assert.equal(again.clearedApiKeys + again.clearedEmbeddingKeys + again.droppedAgentCredentials, 0);
});

test("cloud agent keys: saving answers keys_managed_by_openvault, reads come from OpenVault", async () => {
  const dataDir = join(tmpDir, "agent-data");
  process.env.DATA_DIR = dataDir;
  process.env.DISABLE_SQLITE_AUTO_BACKUP = "true";
  const credentials = await import("../src/lib/cloudAgent/credentials.ts");

  assert.throws(
    () => credentials.saveCloudAgentCredential("devin", "sk-agent-save-attempt", "https://x"),
    (error) => error instanceof KeysManagedByOpenVaultError && error.status === 501
  );
  assert.deepEqual(await credentials.getCloudAgentCredentialFromVault("devin"), { apiKey: DEVIN_SECRET });
  assert.equal(await credentials.getCloudAgentCredentialFromVault("jules"), null);

  const listed = await credentials.listCloudAgentCredentials();
  assert.deepEqual(
    listed.map((row) => row.providerId),
    ["devin"]
  );
  assert.ok(!JSON.stringify(listed).includes(DEVIN_SECRET.slice(-4)), "listing must not carry key material");

  // Nothing was written to FreeRoute's data directory.
  const written = existsSync(dataDir)
    ? readdirSync(dataDir, { recursive: true }).map((name) => join(dataDir, String(name)))
    : [];
  for (const file of written) {
    try {
      assert.ok(!readFileSync(file).includes(Buffer.from("sk-agent-save-attempt")), file);
    } catch (error) {
      if (error?.code !== "EISDIR") throw error;
    }
  }

  // OpenVault down: the named 503, never a fallback.
  process.env.OPENVAULT_URL = await getDeadUrl();
  __resetKeyVaultCacheForTest();
  await assertKeyVaultError(credentials.getCloudAgentCredentialFromVault("devin"), "openvault_keyvault_unreachable");
});

test("semantic cache: the embedding key is never read from the settings table, it is resolved from OpenVault", async () => {
  const dataDir = join(tmpDir, "cache-data");
  process.env.DATA_DIR = dataDir;
  process.env.DISABLE_SQLITE_AUTO_BACKUP = "true";
  const { getDbInstance } = await import("../src/lib/db/core.ts");
  const { getDatabaseSettings, updateDatabaseSettings } = await import("../src/lib/db/databaseSettings.ts");
  const { ensureSemanticCacheDbBridge } = await import("../src/lib/cache/semanticCacheDbBridge.ts");
  const { resolveSemanticCacheConfig } = await import("../open-sse/config/semanticCacheConfig.ts");

  const legacy = "sk-legacy-embedding-key-left-by-an-older-build";
  const db = getDbInstance();
  db.prepare("INSERT OR REPLACE INTO key_value (namespace, key, value) VALUES (?, ?, ?)").run(
    "databaseSettings",
    "cache.semanticCacheEmbeddingApiKey",
    JSON.stringify(legacy)
  );

  // Reading never returns it, and the next settings write overwrites it with "".
  assert.equal(getDatabaseSettings().cache.semanticCacheEmbeddingApiKey, "");
  updateDatabaseSettings({
    cache: {
      semanticCacheEnabled: true,
      semanticCacheVectorEnabled: true,
      semanticCacheEmbeddingProvider: "openai",
      semanticCacheEmbeddingBaseUrl: "https://embeddings.example/v1",
      semanticCacheEmbeddingApiKey: legacy,
    },
  });
  const stored = db
    .prepare("SELECT value FROM key_value WHERE namespace = 'databaseSettings' AND key = 'cache.semanticCacheEmbeddingApiKey'")
    .get();
  assert.equal(JSON.parse(stored.value), "", "a settings write must not persist the key");

  ensureSemanticCacheDbBridge();
  const config = resolveSemanticCacheConfig();
  assert.equal(config.embeddingApiKey, undefined);
  assert.equal(typeof config.embeddingApiKeyResolver, "function");
  assert.equal(await config.embeddingApiKeyResolver(), SECRET, "the key comes from OpenVault");
});

// ── Item 5: the router CLI never writes a provider key ──────────────────────

test("the CLI refuses to store a provider key: keys add, the store writers, setup and rotate", async () => {
  const dataDir = join(tmpDir, "cli-data");
  process.env.DATA_DIR = dataDir;
  const cliSecret = `sk-cli-sentinel-${randomBytes(8).toString("hex")}`;
  const lines = [];
  const originalError = console.error;
  const originalLog = console.log;
  console.error = (...args) => lines.push(args.join(" "));
  console.log = (...args) => lines.push(args.join(" "));
  let addCode;
  try {
    const keys = await import("../bin/cli/commands/keys.mjs");
    addCode = await keys.runKeysAddCommand("openai", cliSecret, {});
    const viaStdin = await keys.runKeysAddCommand("openai", undefined, { stdin: true });
    assert.equal(viaStdin, 1, "no stdin read, no prompt");
  } finally {
    console.error = originalError;
    console.log = originalLog;
  }
  assert.equal(addCode, 1);
  const output = lines.join("\n");
  assert.match(output, /keys_managed_by_openvault/);
  assert.match(output, /http:\/\/127\.0\.0\.1:3010\/keys/);
  assert.ok(!output.includes(cliSecret), "the refusal must not echo the key");

  // setup: an explicit provider request is refused (exit 1, named), no key is written;
  // plain setup and a server --api-key (not a provider key) still succeed.
  const setupLines = [];
  console.error = (...args) => setupLines.push(args.join(" "));
  console.log = (...args) => setupLines.push(args.join(" "));
  let setupRefused;
  let setupPlain;
  let setupServerKey;
  let rotateCode;
  try {
    const { runSetupCommand } = await import("../bin/cli/commands/setup.mjs");
    setupRefused = await runSetupCommand({
      nonInteractive: true,
      addProvider: true,
      provider: "openai",
      apiKey: cliSecret,
    });
    setupPlain = await runSetupCommand({ nonInteractive: true });
    setupServerKey = await runSetupCommand({ nonInteractive: true, apiKey: "server-key-not-a-provider-key" });

    // providers rotate: an API-key connection exists, rotating it is refused and nothing is written.
    const { openOmniRouteDb } = await import("../bin/cli/sqlite.mjs");
    const { ensureProviderSchema } = await import("../bin/cli/provider-store.mjs");
    const { db } = await openOmniRouteDb();
    try {
      ensureProviderSchema(db);
      const now = new Date().toISOString();
      db.prepare(
        "INSERT INTO provider_connections (id, provider, auth_type, name, priority, is_active, created_at, updated_at) " +
          "VALUES ('conn-rotate', 'openai', 'apikey', 'rotate-me', 1, 1, ?, ?)"
      ).run(now, now);
    } finally {
      db.close();
    }
    const { runProvidersRotateCommand } = await import("../bin/cli/commands/providers.mjs");
    rotateCode = await runProvidersRotateCommand("rotate-me", { newKey: cliSecret, yes: true });
  } finally {
    console.error = originalError;
    console.log = originalLog;
  }
  assert.equal(setupRefused, 1);
  assert.equal(setupPlain, 0);
  assert.equal(setupServerKey, 0);
  assert.equal(rotateCode, 1);
  const setupOutput = setupLines.join("\n");
  assert.match(setupOutput, /keys_managed_by_openvault/);
  assert.ok(!setupOutput.includes(cliSecret), "the refusal must not echo the key");

  const store = await import("../bin/cli/provider-store.mjs");
  for (const call of [
    () => store.upsertApiKeyProviderConnection({}, { provider: "openai", apiKey: cliSecret }),
    () => store.updateProviderApiKey({}, "conn-1", cliSecret),
  ]) {
    assert.throws(call, (error) => error.code === "keys_managed_by_openvault" && error.status === 501);
  }

  // Nothing reached disk: no database or file under the CLI data directory holds the key.
  const files = existsSync(dataDir)
    ? readdirSync(dataDir, { recursive: true }).map((name) => join(dataDir, String(name)))
    : [];
  for (const file of files) {
    try {
      assert.ok(!readFileSync(file).includes(Buffer.from(cliSecret)), file);
    } catch (error) {
      if (error?.code !== "EISDIR") throw error;
    }
  }
});

test("the CLI sources never import the provider key writers", () => {
  const sources = ["keys.mjs", "setup.mjs", "providers.mjs"].map((name) =>
    readFileSync(new URL(`../bin/cli/commands/${name}`, import.meta.url), "utf8")
  );
  for (const source of sources) {
    assert.doesNotMatch(source, /upsertApiKeyProviderConnection|updateProviderApiKey|encryptCredential/);
  }
});
