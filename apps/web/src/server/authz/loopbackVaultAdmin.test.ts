/**
 * Loopback console attaches the admin file token only for vault bootstrap.
 * Fixture token only. No live credential. No network.
 *
 * Run: npx tsx --test src/server/authz/loopbackVaultAdmin.test.ts
 */

import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { test } from "node:test";
import { tmpdir } from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";

import { runAuthzPipeline } from "./pipeline.ts";
import {
  applyLoopbackAdminHeader,
  isLoopbackVaultBootstrap,
  localAdminTokenPath,
  readLocalAdminToken,
  sanitizeAdminToken,
  vaultBackendPath,
} from "./loopbackVaultAdmin.ts";

const TOKEN = "unit-loopback-admin-not-a-live-token";
const CALLER = "caller-supplied-not-the-file";

function upstreamAdmin(res: Response): string | null {
  return res.headers.get("x-middleware-request-x-openvault-admin");
}

function assertTokenNotInBody(res: Response, token: string): Promise<void> {
  return res.text().then((body) => {
    assert.equal(body.includes(token), false);
  });
}

/** The rewrite header is the only place the fixture may appear. Next strips it. */
function assertTokenOnlyOnRewrite(res: Response, token: string): void {
  for (const [key, value] of res.headers) {
    if (!value.includes(token)) continue;
    assert.equal(key, "x-middleware-request-x-openvault-admin");
  }
}

async function withTokenFile(
  present: "token" | "empty" | "missing" | "broken",
  run: () => Promise<void>,
): Promise<void> {
  const dir = mkdtempSync(path.join(tmpdir(), "ov-admin-"));
  const file = path.join(dir, "admin_token");
  const prevPath = process.env.OPENVAULT_ADMIN_TOKEN_PATH;
  const prevHome = process.env.OPENVAULT_HOME;
  const prevTrust = process.env.OPENVAULT_TRUST_PROXY;
  process.env.OPENVAULT_ADMIN_TOKEN_PATH = file;
  delete process.env.OPENVAULT_HOME;
  if (present === "token") writeFileSync(file, `${TOKEN}\n`, "utf8");
  if (present === "empty") writeFileSync(file, " \n", "utf8");
  if (present === "broken") writeFileSync(file, `${TOKEN}\nsecond`, "utf8");
  try {
    await run();
  } finally {
    if (prevPath === undefined) delete process.env.OPENVAULT_ADMIN_TOKEN_PATH;
    else process.env.OPENVAULT_ADMIN_TOKEN_PATH = prevPath;
    if (prevHome === undefined) delete process.env.OPENVAULT_HOME;
    else process.env.OPENVAULT_HOME = prevHome;
    if (prevTrust === undefined) delete process.env.OPENVAULT_TRUST_PROXY;
    else process.env.OPENVAULT_TRUST_PROXY = prevTrust;
    rmSync(dir, { recursive: true, force: true });
  }
}

function call(url: string, method = "GET", headers: Record<string, string> = {}) {
  return runAuthzPipeline(new NextRequest(url, { method, headers }), { enforce: true });
}

test("bootstrap paths are exact", () => {
  assert.equal(vaultBackendPath("/ov-api/api/vault/status"), "/api/vault/status");
  assert.equal(vaultBackendPath("/ov-api/api/../api/keys"), "");
  assert.equal(isLoopbackVaultBootstrap("GET", "/ov-api/api/vault/status"), true);
  assert.equal(isLoopbackVaultBootstrap("get", "/api/vault/status/"), true);
  assert.equal(isLoopbackVaultBootstrap("POST", "/ov-api/api/vault/unseal"), true);
  assert.equal(
    isLoopbackVaultBootstrap("POST", "/ov-api/api/vault/webauthn/unseal/begin"),
    true,
  );
  assert.equal(
    isLoopbackVaultBootstrap("POST", "/ov-api/api/vault/webauthn/unseal/finish"),
    true,
  );
  assert.equal(isLoopbackVaultBootstrap("GET", "/ov-api/api/keys"), true);
  assert.equal(isLoopbackVaultBootstrap("GET", "/ov-api/api/keys?x=1"), true);
  assert.equal(isLoopbackVaultBootstrap("POST", "/ov-api/api/keys"), false);
  assert.equal(isLoopbackVaultBootstrap("GET", "/ov-api/api/keys/abc/secret"), false);
  assert.equal(isLoopbackVaultBootstrap("POST", "/ov-api/api/vault/lock"), false);
  assert.equal(
    isLoopbackVaultBootstrap("POST", "/ov-api/api/vault/webauthn/register/begin"),
    false,
  );
  assert.equal(isLoopbackVaultBootstrap("HEAD", "/ov-api/api/vault/status"), false);
  assert.equal(sanitizeAdminToken(`  ${TOKEN}\n`), TOKEN);
  assert.equal(sanitizeAdminToken(`${TOKEN}\nnope`), "");
  assert.equal(sanitizeAdminToken(""), "");
});

test("token path follows the vault home override", () => {
  const home = localAdminTokenPath(
    { OPENVAULT_HOME: "~/vault-home", OPENVAULT_ADMIN_TOKEN_PATH: "" },
    "/home/founder",
  );
  assert.equal(home, path.join("/home/founder", "vault-home", "admin_token"));
  const override = localAdminTokenPath(
    { OPENVAULT_ADMIN_TOKEN_PATH: "~/secret-file" },
    "/home/founder",
  );
  assert.equal(override, path.join("/home/founder", "secret-file"));
});

test("apply refuses a non-loopback host and a mutation", () => {
  const headers = new Headers();
  assert.equal(
    applyLoopbackAdminHeader(headers, {
      method: "GET",
      pathname: "/ov-api/api/vault/status",
      urlHost: "203.0.113.10",
      token: TOKEN,
    }),
    false,
  );
  assert.equal(headers.get("x-openvault-admin"), null);
  assert.equal(
    applyLoopbackAdminHeader(headers, {
      method: "POST",
      pathname: "/ov-api/api/keys",
      urlHost: "127.0.0.1",
      token: TOKEN,
    }),
    false,
  );
  assert.equal(headers.get("x-openvault-admin"), null);
  assert.equal(
    applyLoopbackAdminHeader(headers, {
      method: "GET",
      pathname: "/ov-api/api/keys",
      urlHost: "127.0.0.1",
      token: TOKEN,
    }),
    true,
  );
  assert.equal(headers.get("x-openvault-admin"), TOKEN);
});

test("loopback status, unseal, Hello, and key list attach; mutations do not", async () => {
  await withTokenFile("token", async () => {
    assert.equal(readLocalAdminToken(), TOKEN);

    const status = await call("http://127.0.0.1:3010/ov-api/api/vault/status?fresh=1");
    assert.equal(status.status, 200);
    assert.equal(upstreamAdmin(status), TOKEN);
    assertTokenOnlyOnRewrite(status, TOKEN);

    const unseal = await call("http://127.0.0.1:3010/ov-api/api/vault/unseal", "POST", {
      origin: "http://127.0.0.1:3010",
      "content-type": "application/json",
    });
    assert.equal(upstreamAdmin(unseal), TOKEN);

    const helloBegin = await call(
      "http://127.0.0.1:3010/ov-api/api/vault/webauthn/unseal/begin",
      "POST",
      { origin: "http://localhost:3010" },
    );
    assert.equal(upstreamAdmin(helloBegin), TOKEN);
    const helloFinish = await call(
      "http://127.0.0.1:3010/ov-api/api/vault/webauthn/unseal/finish",
      "POST",
      { origin: "http://127.0.0.1:3010" },
    );
    assert.equal(upstreamAdmin(helloFinish), TOKEN);

    const listed = await call("http://localhost:3010/ov-api/api/keys");
    assert.equal(upstreamAdmin(listed), TOKEN);

    const v6 = await call("http://[::1]:3010/ov-api/api/vault/status");
    assert.equal(upstreamAdmin(v6), TOKEN);

    const secret = await call("http://127.0.0.1:3010/ov-api/api/keys/abc/secret");
    assert.equal(upstreamAdmin(secret), null);
    const create = await call("http://127.0.0.1:3010/ov-api/api/keys", "POST", {
      origin: "http://127.0.0.1:3010",
      "content-type": "application/json",
    });
    assert.equal(upstreamAdmin(create), null);
    const lock = await call("http://127.0.0.1:3010/ov-api/api/vault/lock", "POST", {
      origin: "http://127.0.0.1:3010",
    });
    assert.equal(upstreamAdmin(lock), null);
    const register = await call(
      "http://127.0.0.1:3010/ov-api/api/vault/webauthn/register/begin",
      "POST",
      { origin: "http://127.0.0.1:3010" },
    );
    assert.equal(upstreamAdmin(register), null);

    const overwritten = await call("http://127.0.0.1:3010/ov-api/api/vault/status", "GET", {
      "x-openvault-admin": CALLER,
    });
    assert.equal(upstreamAdmin(overwritten), TOKEN);
    const kept = await call("http://127.0.0.1:3010/ov-api/api/keys", "POST", {
      origin: "http://127.0.0.1:3010",
      "x-openvault-admin": CALLER,
    });
    assert.equal(upstreamAdmin(kept), CALLER);
    await assertTokenNotInBody(kept, TOKEN);
  });
});

test("non-loopback and a spoofed forwarded header do not attach", async () => {
  await withTokenFile("token", async () => {
    const remote = await call("http://203.0.113.10:3010/ov-api/api/vault/status", "GET", {
      "x-forwarded-for": "127.0.0.1",
      "x-real-ip": "127.0.0.1",
      host: "127.0.0.1:3010",
    });
    assert.equal(remote.status, 403);
    assert.equal(upstreamAdmin(remote), null);
    await assertTokenNotInBody(remote, TOKEN);

    const remoteUnseal = await call("http://192.168.1.20:3010/ov-api/api/vault/unseal", "POST", {
      origin: "http://192.168.1.20:3010",
      "x-forwarded-for": "127.0.0.1",
    });
    assert.equal(remoteUnseal.status, 403);
    assert.equal(upstreamAdmin(remoteUnseal), null);
    await assertTokenNotInBody(remoteUnseal, TOKEN);

    const remoteKeys = await call("http://10.0.0.8:3010/ov-api/api/keys");
    assert.equal(remoteKeys.status, 403);
    assert.equal(upstreamAdmin(remoteKeys), null);

    process.env.OPENVAULT_TRUST_PROXY = "1";
    const spoofed = await call("http://203.0.113.10:3010/ov-api/api/vault/unseal", "POST", {
      origin: "http://203.0.113.10:3010",
      "x-forwarded-for": "127.0.0.1",
    });
    assert.equal(upstreamAdmin(spoofed), null);
    await assertTokenNotInBody(spoofed, TOKEN);
  });
});

test("missing, empty, or broken token file does not invent a header", async () => {
  await withTokenFile("missing", async () => {
    const res = await call("http://127.0.0.1:3010/ov-api/api/vault/status");
    assert.equal(res.status, 200);
    assert.equal(upstreamAdmin(res), null);
  });
  await withTokenFile("empty", async () => {
    const res = await call("http://127.0.0.1:3010/ov-api/api/keys");
    assert.equal(upstreamAdmin(res), null);
  });
  await withTokenFile("broken", async () => {
    const res = await call("http://127.0.0.1:3010/ov-api/api/vault/unseal", "POST", {
      origin: "http://127.0.0.1:3010",
    });
    assert.equal(upstreamAdmin(res), null);
  });
});

test("unseal without a loopback origin does not attach", async () => {
  await withTokenFile("token", async () => {
    const res = await call("http://127.0.0.1:3010/ov-api/api/vault/unseal", "POST");
    assert.equal(res.status, 403);
    assert.equal(upstreamAdmin(res), null);
    await assertTokenNotInBody(res, TOKEN);
  });
});
