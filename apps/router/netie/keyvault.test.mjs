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
  OpenVaultUnreachableError,
  __resetKeyVaultCacheForTest,
} from "../src/lib/netie/keyvault.ts";

const ADMIN_TOKEN = `adm_${randomBytes(24).toString("hex")}`;
const KEY_ID = "key_unit_test";
const SECRET = "sk-unit-test-not-real";
const ENDPOINT_KEY_ID = "key_unit_endpoint";
const ENDPOINT_SECRET = "sk-unit-endpoint-not-real";
const ENDPOINT_BASE_URL = "http://127.0.0.1:9/v1";
const VALID_CLIENT_TOKEN = `ovtok_unit_${randomBytes(8).toString("hex")}`;

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
            ],
          })
        );
        return;
      }

      const secretMatch = /^\/api\/keys\/([^/]+)\/secret$/.exec(url.pathname);
      const secrets = { [KEY_ID]: SECRET, [ENDPOINT_KEY_ID]: ENDPOINT_SECRET };
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
