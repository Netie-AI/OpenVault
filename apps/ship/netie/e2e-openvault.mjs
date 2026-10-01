// Modified by Netie AI, 2026: FreeBuild naming; upstream names and links removed from shipped text.
// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// FreeBuild end-to-end test against a REAL OpenVault. Run with `npm run test:e2e`
// after `npm run build`.
//
// Needs `uv` on PATH and the OpenVault repo checked out around this workspace
// (apps/ship inside it), or OPENVAULT_REPO pointing at that repo. It starts:
//   - the real OpenVault API (OpenMW, `uv run openmw console`) on a temp home,
//   - the built FreeBuild API (apps/api/dist) on an embedded PGlite database,
//   - the built FreeBuild dashboard (`next start`),
// each on a free loopback port, and removes every process and temp dir at the
// end, pass or fail.
//
// What it proves:
//   1. A Cloudflare token added to OpenVault (provider "custom", label
//      "Cloudflare API token") shows up in FreeBuild's credential listings,
//      masked, and FreeBuild's DNS zone check resolves it and sends exactly the
//      vault secret as the Cloudflare bearer token. OpenVault audits the reveal.
//   2. FreeBuild refuses to store a Cloudflare key (501 keys_managed_by_openvault)
//      and refuses hosted-cloud routes (501 hosted_cloud_disabled).
//   3. A wrong OpenVault admin token is 503 openvault_admin_token_rejected, and
//      a stopped OpenVault is 503 openvault_keyvault_unreachable.
//   4. The secret never lands in FreeBuild's data dir or logs.
//   5. The dashboard home page says FreeBuild and never the upstream names.
//   6. A Cloudflare key OpenVault holds with custody "tenant" is never listed,
//      revealed or sent to Cloudflare, even with a better priority.
//   7. The dashboard binds to 127.0.0.1 when FREEBUILD_DASHBOARD_HOST is unset,
//      and its onboarding and 404 pages carry no upstream names.
//
// The one stand-in: api.cloudflare.com. FreeBuild's API process is started
// with a small preload that sends requests for that host to a local stub, so
// the test can read the bearer token FreeBuild sends and needs no Cloudflare
// account. Everything between OpenVault and that outbound call is the real code.

import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, openSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { networkInterfaces, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const HERE = dirname(fileURLToPath(import.meta.url));
const SHIP_ROOT = dirname(HERE);
const API_DIR = join(SHIP_ROOT, "apps/api");
const DASHBOARD_DIR = join(SHIP_ROOT, "apps/dashboard");
const OPENVAULT_REPO = resolve(process.env.OPENVAULT_REPO || join(SHIP_ROOT, "../.."));
const OPENMW_DIR = join(OPENVAULT_REPO, "OpenMW");

const KEY_LABEL = "Cloudflare API token";
const SECRET = `e2e-cloudflare-token-${randomBytes(12).toString("hex")}`;
const TENANT_LABEL = "Cloudflare tenant token";
const TENANT_SECRET = `e2e-tenant-cloudflare-token-${randomBytes(12).toString("hex")}`;
const ZONE = { id: "e2e-zone-id", name: "e2e-zone.test", status: "active" };
const CF_HOST = "https://api.cloudflare.com";

// ─── Process and temp-dir bookkeeping ──────────────────────────────────────

const children = new Map(); // name -> ChildProcess
let tmpRoot;
let cfStub;

function log(msg) {
  console.log(`[e2e] ${msg}`);
}

function pass(msg) {
  console.log(`[e2e] PASS ${msg}`);
}

/** Start a process in its own group so the whole tree (uv -> python, npm -> next) can be stopped. */
function startProcess(name, cmd, args, opts) {
  const logPath = join(tmpRoot, "logs", `${name}.log`);
  const fd = openSync(logPath, "a");
  const child = spawn(cmd, args, { ...opts, detached: true, stdio: ["ignore", fd, fd] });
  child.logPath = logPath;
  child.exited = new Promise((r) => child.on("exit", (code, signal) => r({ code, signal })));
  children.set(name, child);
  return child;
}

async function stopProcess(name) {
  const child = children.get(name);
  if (!child) return;
  children.delete(name);
  if (child.exitCode !== null || child.signalCode !== null) return;
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    return;
  }
  const done = await Promise.race([child.exited, sleep(10_000).then(() => null)]);
  if (!done) {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {}
    await child.exited;
  }
}

function killAllSync() {
  for (const child of children.values()) {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {}
  }
  children.clear();
}

function tail(path, lines = 40) {
  try {
    return readFileSync(path, "utf8").split("\n").slice(-lines).join("\n");
  } catch {
    return "(no log)";
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function freePort() {
  const srv = createServer();
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  const { port } = srv.address();
  await new Promise((r) => srv.close(r));
  return port;
}

async function waitFor(url, { child, timeoutMs, ok = (res) => res.ok }) {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    if (child && child.exitCode !== null) {
      throw new Error(`${url}: process exited (code ${child.exitCode}) before it was ready:\n${tail(child.logPath)}`);
    }
    try {
      const res = await fetch(url, { redirect: "manual" });
      if (ok(res)) return res;
      last = `HTTP ${res.status}`;
    } catch (err) {
      last = String(err?.cause?.code ?? err);
    }
    await sleep(500);
  }
  throw new Error(`${url} never became ready (${last})${child ? `:\n${tail(child.logPath)}` : ""}`);
}

async function json(res) {
  const text = await res.text();
  try {
    return { text, body: JSON.parse(text) };
  } catch {
    return { text, body: null };
  }
}

/** Every file under `dir` whose bytes contain `needle`. Same as `grep -rla`. */
function filesContaining(dir, needle) {
  const hits = [];
  const want = Buffer.from(needle);
  const walk = (d) => {
    for (const entry of readdirSync(d, { withFileTypes: true })) {
      const p = join(d, entry.name);
      if (entry.isDirectory()) walk(p);
      else if (entry.isFile() && readFileSync(p).includes(want)) hits.push(p);
    }
  };
  if (existsSync(dir)) walk(dir);
  return hits;
}

// ─── Cloudflare stand-in ───────────────────────────────────────────────────

const cfSeen = []; // { path, authorization }

function startCloudflareStub() {
  const server = createServer((req, res) => {
    cfSeen.push({ path: req.url, authorization: req.headers.authorization ?? "" });
    const url = new URL(req.url, "http://127.0.0.1");
    res.setHeader("content-type", "application/json");
    // Only a request carrying the vault secret sees the zone, so a match proves
    // FreeBuild resolved exactly that secret.
    if (req.headers.authorization !== `Bearer ${SECRET}`) {
      res.writeHead(403);
      res.end(JSON.stringify({ success: false, errors: [{ code: 9109, message: "Invalid access token" }], messages: [], result: null }));
      return;
    }
    if (url.pathname === "/client/v4/zones") {
      const result = url.searchParams.get("name") === ZONE.name ? [ZONE] : [];
      res.end(JSON.stringify({ success: true, errors: [], messages: [], result }));
      return;
    }
    res.writeHead(404);
    res.end(JSON.stringify({ success: false, errors: [{ code: 7003, message: "not found" }], messages: [], result: null }));
  });
  return new Promise((r) => server.listen(0, "127.0.0.1", () => r(server)));
}

/** Preloaded into the FreeBuild API: send api.cloudflare.com requests to the stub. */
const CF_REDIRECT_HOOK = `
const target = process.env.E2E_CLOUDFLARE_STUB_URL;
const realFetch = globalThis.fetch;
globalThis.fetch = (input, init) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  if (target && url.startsWith(${JSON.stringify(CF_HOST)} + "/")) {
    const redirected = target + url.slice(${JSON.stringify(CF_HOST)}.length);
    return typeof input === "string" || input instanceof URL
      ? realFetch(redirected, init)
      : realFetch(new Request(redirected, input), init);
  }
  return realFetch(input, init);
};
`;

// ─── The run ───────────────────────────────────────────────────────────────

async function main() {
  const uv = spawnSync("uv", ["--version"], { encoding: "utf8", timeout: 30_000 });
  if (uv.error || uv.status !== 0) {
    throw new Error("`uv` is not on PATH. The E2E starts the real OpenVault with `uv run openmw console`.");
  }
  if (!existsSync(join(OPENMW_DIR, "pyproject.toml"))) {
    throw new Error(`OpenVault's OpenMW not found at ${OPENMW_DIR}. Set OPENVAULT_REPO to the OpenVault repo.`);
  }
  for (const built of [join(API_DIR, "dist/index.js"), join(DASHBOARD_DIR, ".next/BUILD_ID")]) {
    if (!existsSync(built)) throw new Error(`${built} is missing. Run \`npm run build\` first.`);
  }

  tmpRoot = await mkdtemp(join(tmpdir(), "freebuild-e2e-"));
  mkdirSync(join(tmpRoot, "logs"));
  const ovHome = join(tmpRoot, "openvault-home");
  const fbData = join(tmpRoot, "freebuild-data");
  mkdirSync(fbData);
  log(`temp dir ${tmpRoot}`);

  // ── a. Real OpenVault ──
  const ovPort = await freePort();
  const ovUrl = `http://127.0.0.1:${ovPort}`;
  // OpenVault's own tests set OPENVAULT_KEY to a Fernet key. The console does
  // not read it today (the master key is generated at $OPENVAULT_HOME/master.key);
  // it is set here to match how OpenVault's suite runs.
  const fernetKey = randomBytes(32).toString("base64url") + "=";
  const ovEnv = { ...process.env, OPENVAULT_HOME: ovHome, OPENVAULT_KEY: fernetKey };
  delete ovEnv.OPENVAULT_ADMIN_TOKEN_PATH;
  const ov = startProcess(
    "openvault",
    "uv",
    ["run", "openmw", "console", "--port", String(ovPort), "--no-open-browser", "--mock-health", "--precheck-interval", "3600"],
    { cwd: OPENMW_DIR, env: ovEnv },
  );
  log(`starting OpenVault on ${ovUrl} (uv run openmw console)`);
  await waitFor(`${ovUrl}/api/healthz`, { child: ov, timeoutMs: 180_000 });
  pass(`OpenVault answers /api/healthz on ${ovUrl}`);

  const ovTokenPath = join(ovHome, "admin_token");
  assert.ok(existsSync(ovTokenPath), "OpenVault created $OPENVAULT_HOME/admin_token on first start");
  assert.equal(statSync(ovTokenPath).mode & 0o777, 0o600, "admin_token is mode 0600");
  const adminToken = readFileSync(ovTokenPath, "utf8").trim();
  pass("OpenVault created a mode-0600 admin_token");

  {
    const res = await fetch(`${ovUrl}/api/keys`);
    const { body } = await json(res);
    assert.equal(res.status, 401, "GET /api/keys without X-OpenVault-Admin");
    assert.equal(body?.error?.type, "openvault_unauthenticated");
    pass("OpenVault refuses /api/keys without the admin token (401 openvault_unauthenticated)");
  }

  // ── b. Add the Cloudflare key to OpenVault through its real API ──
  // POST /api/keys takes {label, provider, secret, role?, base_url?, ...}. It
  // stores without probing the key. "custom" has no catalog base_url, and an
  // empty base_url is accepted unless the label looks like a site password.
  let keyId;
  {
    const res = await fetch(`${ovUrl}/api/keys`, {
      method: "POST",
      headers: { "content-type": "application/json", "X-OpenVault-Admin": adminToken },
      body: JSON.stringify({ label: KEY_LABEL, provider: "custom", secret: SECRET }),
    });
    const { text, body } = await json(res);
    assert.equal(res.status, 200, `POST /api/keys: ${text}`);
    assert.ok(body?.id, "OpenVault returned a key id");
    assert.ok(!text.includes(SECRET), "OpenVault's create response must not echo the secret");
    keyId = body.id;
    pass(`added "${KEY_LABEL}" to OpenVault as ${keyId} (provider custom, masked ${body.masked_secret})`);
  }

  // A tenant's Cloudflare key: same provider and label prefix, better priority.
  // OpenVault's own router never spends it (fallback.py _is_available,
  // store.py pooled_ordered); FreeBuild must not either.
  let tenantKeyId;
  {
    const res = await fetch(`${ovUrl}/api/keys`, {
      method: "POST",
      headers: { "content-type": "application/json", "X-OpenVault-Admin": adminToken },
      body: JSON.stringify({ label: TENANT_LABEL, provider: "custom", secret: TENANT_SECRET, priority: 1, custody: "tenant" }),
    });
    const { text, body } = await json(res);
    assert.equal(res.status, 200, `POST /api/keys (tenant): ${text}`);
    assert.ok(body?.id, "OpenVault returned the tenant key id");
    tenantKeyId = body.id;
    const list = await fetch(`${ovUrl}/api/keys`, { headers: { "X-OpenVault-Admin": adminToken } });
    const listed = await json(list);
    const row = (listed.body?.keys ?? []).find((k) => k.id === tenantKeyId);
    assert.equal(row?.custody, "tenant", `OpenVault lists ${tenantKeyId} with custody tenant: ${listed.text}`);
    pass(`added "${TENANT_LABEL}" to OpenVault as ${tenantKeyId} (custody tenant, priority 1)`);
  }

  // FreeBuild reads its own copy of the token so the wrong-token case can
  // change it without touching OpenVault's file.
  const fbTokenPath = join(tmpRoot, "freebuild-admin-token");
  writeFileSync(fbTokenPath, adminToken, { mode: 0o600 });

  // ── c. Built FreeBuild API ──
  cfStub = await startCloudflareStub();
  const apiPort = await freePort();
  const apiUrl = `http://127.0.0.1:${apiPort}`;
  const apiEnv = {
    ...process.env,
    NODE_ENV: "production",
    PORT: String(apiPort),
    OPENSHIP_API_HOST: "127.0.0.1",
    INTERNAL_TOKEN: randomBytes(16).toString("hex"),
    PGLITE_DATA_DIR: join(fbData, "pglite"),
    OPENVAULT_URL: ovUrl,
    OPENVAULT_ADMIN_TOKEN_PATH: fbTokenPath,
    // Disposable loopback instance only: lets this script call admin routes
    // without a login flow. Never set in a real deployment.
    OPENSHIP_ALLOW_ZERO_AUTH: "true",
    E2E_CLOUDFLARE_STUB_URL: `http://127.0.0.1:${cfStub.address().port}`,
  };
  delete apiEnv.OPENVAULT_HOME;
  delete apiEnv.DATABASE_URL;
  const api = startProcess(
    "freebuild-api",
    process.execPath,
    ["--import", "tsx", "--import", `data:text/javascript,${encodeURIComponent(CF_REDIRECT_HOOK)}`, "dist/index.js"],
    { cwd: API_DIR, env: apiEnv },
  );
  log(`starting FreeBuild API on ${apiUrl}`);
  await waitFor(`${apiUrl}/api/health`, { child: api, timeoutMs: 90_000 });
  pass(`FreeBuild API answers /api/health on ${apiUrl}`);

  // ── d. The real credential read path ──
  {
    const res = await fetch(`${apiUrl}/api/credentials`);
    const { text, body } = await json(res);
    assert.equal(res.status, 200, `GET /api/credentials: ${text}`);
    const row = body.data.find((c) => c.id === keyId);
    assert.ok(row, `GET /api/credentials lists the OpenVault key ${keyId}: ${text}`);
    assert.equal(row.provider, "cloudflare");
    assert.equal(row.name, KEY_LABEL);
    assert.equal(row.status, "active");
    assert.deepEqual(Object.keys(row.secretsMasked), ["apiToken"]);
    assert.ok(!text.includes(SECRET), "the listing must not carry the secret");
    pass(`GET /api/credentials lists ${keyId} as cloudflare "${KEY_LABEL}", secret masked`);
    assert.ok(!body.data.some((c) => c.id === tenantKeyId), `GET /api/credentials must not list the tenant key: ${text}`);
    assert.ok(!text.includes(TENANT_LABEL), "the listing must not name the tenant key");
    pass(`GET /api/credentials does not list the tenant-custody key ${tenantKeyId}`);
  }
  {
    const res = await fetch(`${apiUrl}/api/dns/credentials`);
    const { text, body } = await json(res);
    assert.equal(res.status, 200, `GET /api/dns/credentials: ${text}`);
    assert.ok(body.data.some((c) => c.id === keyId && c.provider === "cloudflare"), `DNS listing has ${keyId}: ${text}`);
    assert.ok(!text.includes(SECRET), "the DNS listing must not carry the secret");
    pass(`GET /api/dns/credentials lists ${keyId}, secret masked`);
    assert.ok(!body.data.some((c) => c.id === tenantKeyId), `DNS listing must not have the tenant key: ${text}`);
    pass(`GET /api/dns/credentials does not list the tenant-custody key ${tenantKeyId}`);
  }
  {
    const auditPath = join(ovHome, "secret_audit.jsonl");
    const revealsBefore = readAudit(auditPath).filter((e) => e.event === "secret_reveal").length;
    const res = await fetch(`${apiUrl}/api/dns/verify-zone`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ hostname: `app.${ZONE.name}` }),
    });
    const { text, body } = await json(res);
    assert.equal(res.status, 200, `POST /api/dns/verify-zone: ${text}`);
    assert.equal(body.matched, true, `zone matched: ${text}`);
    assert.equal(body.credentialId, keyId);
    assert.equal(body.zoneName, ZONE.name);
    assert.ok(!text.includes(SECRET), "verify-zone must not return the secret");
    const bearers = new Set(cfSeen.map((s) => s.authorization));
    assert.deepEqual([...bearers], [`Bearer ${SECRET}`], "FreeBuild sent exactly the vault secret to Cloudflare");
    pass(`POST /api/dns/verify-zone resolved ${keyId} from OpenVault; the bearer sent equals the vault secret`);

    const audit = readAudit(auditPath);
    const reveals = audit.filter((e) => e.event === "secret_reveal" && e.key_id === keyId);
    assert.ok(reveals.length > revealsBefore, `OpenVault audited a secret_reveal for ${keyId}`);
    assert.ok(!readFileSync(auditPath, "utf8").includes(SECRET), "the audit log must not hold the secret");
    pass(`OpenVault logged secret_reveal for ${keyId} (client ${reveals.at(-1).client}); audit holds no secret`);

    assert.ok(cfSeen.length > 0, "the Cloudflare stub saw requests");
    assert.ok(!cfSeen.some((s) => s.authorization.includes(TENANT_SECRET)), "the tenant secret was sent to Cloudflare");
    const tenantReveals = audit.filter((e) => e.event === "secret_reveal" && e.key_id === tenantKeyId);
    assert.deepEqual(tenantReveals, [], `FreeBuild revealed the tenant key ${tenantKeyId}`);
    pass(`tenant-custody key ${tenantKeyId} never revealed by OpenVault nor sent to Cloudflare (${cfSeen.length} stub calls)`);
  }

  // ── e. Negative cases ──
  {
    const res = await fetch(`${apiUrl}/api/credentials`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ provider: "cloudflare", name: "e2e", values: { apiToken: "must-not-be-stored" } }),
    });
    const { text, body } = await json(res);
    assert.equal(res.status, 501, `POST /api/credentials cloudflare: ${text}`);
    assert.equal(body?.error?.code, "keys_managed_by_openvault");
    pass("POST /api/credentials (cloudflare) -> 501 keys_managed_by_openvault");
  }
  {
    const res = await fetch(`${apiUrl}/api/dns/credentials`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ provider: "cloudflare", name: "e2e", apiToken: "must-not-be-stored" }),
    });
    const { text, body } = await json(res);
    assert.equal(res.status, 501, `POST /api/dns/credentials: ${text}`);
    assert.equal(body?.error?.code, "keys_managed_by_openvault");
    pass("POST /api/dns/credentials -> 501 keys_managed_by_openvault");
  }
  {
    const res = await fetch(`${apiUrl}/api/cloud/status`);
    const { text, body } = await json(res);
    assert.equal(res.status, 501, `GET /api/cloud/status: ${text}`);
    assert.equal(body?.error?.code, "hosted_cloud_disabled");
    pass("GET /api/cloud/status -> 501 hosted_cloud_disabled");
  }
  {
    const wrong = randomBytes(32).toString("base64url");
    writeFileSync(fbTokenPath, wrong, { mode: 0o600 });
    try {
      for (const [method, path, payload] of [
        ["GET", "/api/credentials"],
        ["POST", "/api/dns/verify-zone", { hostname: `app.${ZONE.name}` }],
      ]) {
        const res = await fetch(`${apiUrl}${path}`, {
          method,
          headers: payload ? { "content-type": "application/json" } : {},
          body: payload ? JSON.stringify(payload) : undefined,
        });
        const { text, body } = await json(res);
        assert.equal(res.status, 503, `${method} ${path} with a wrong token: ${text}`);
        assert.equal(body?.code, "openvault_admin_token_rejected", text);
        assert.ok(!text.includes(wrong), "the token must not appear in the response");
        pass(`${method} ${path} with a wrong admin token -> 503 openvault_admin_token_rejected`);
      }
    } finally {
      writeFileSync(fbTokenPath, adminToken, { mode: 0o600 });
    }
  }

  // ── g. Dashboard, while the API is still up ──
  await checkDashboard(apiUrl);

  await stopProcess("openvault");
  {
    const res = await fetch(`${apiUrl}/api/credentials`);
    const { text, body } = await json(res);
    assert.equal(res.status, 503, `GET /api/credentials with OpenVault stopped: ${text}`);
    assert.equal(body?.code, "openvault_keyvault_unreachable", text);
    pass("GET /api/credentials with OpenVault stopped -> 503 openvault_keyvault_unreachable");
  }

  // ── f. The secret never reached FreeBuild's disk or logs ──
  // Stop the API first so PGlite flushes everything it holds to disk.
  await stopProcess("freebuild-api");
  {
    const dataHits = filesContaining(fbData, SECRET);
    assert.deepEqual(dataHits, [], `secret found in FreeBuild data: ${dataHits.join(", ")}`);
    const logHits = filesContaining(join(tmpRoot, "logs"), SECRET).filter((p) => !p.endsWith("openvault.log"));
    assert.deepEqual(logHits, [], `secret found in FreeBuild logs: ${logHits.join(", ")}`);
    const tenantHits = [...filesContaining(fbData, TENANT_SECRET), ...filesContaining(join(tmpRoot, "logs"), TENANT_SECRET)];
    assert.deepEqual(tenantHits, [], `tenant secret found in FreeBuild files: ${tenantHits.join(", ")}`);
    const fileCount = countFiles(fbData);
    assert.ok(fileCount > 0, "the PGlite data dir was written");
    // Control: the scan does see plain strings inside PGlite's files. The
    // credential table's name is in the catalog of every FreeBuild database.
    assert.ok(filesContaining(fbData, "secrets_enc").length > 0, "the scan finds a known string in PGlite files");
    pass(`secret absent from FreeBuild data dir (${fileCount} files scanned) and its logs`);
  }
}

/** g. The built dashboard, pointed at the running FreeBuild API. */
async function checkDashboard(apiUrl) {
  const dashPort = await freePort();
  const dashUrl = `http://127.0.0.1:${dashPort}`;
  // No FREEBUILD_DASHBOARD_HOST: the run proves the default bind, not ours.
  const dashEnv = {
    ...process.env,
    PORT: String(dashPort),
    NODE_ENV: "production",
    OPENSHIP_LOCAL_API_URL: apiUrl,
  };
  delete dashEnv.FREEBUILD_DASHBOARD_HOST;
  const dashboard = startProcess("freebuild-dashboard", "npm", ["run", "start"], { cwd: DASHBOARD_DIR, env: dashEnv });
  log(`starting FreeBuild dashboard on ${dashUrl} (FREEBUILD_DASHBOARD_HOST unset)`);
  await waitFor(`${dashUrl}/`, { child: dashboard, timeoutMs: 90_000, ok: (res) => res.status < 500 });
  {
    const res = await fetch(`${dashUrl}/`);
    const html = await res.text();
    assert.equal(res.status, 200, `GET / (after redirects, ${res.url}) answered ${res.status}`);
    assert.match(html, /FreeBuild/, "the dashboard HTML names FreeBuild");
    assertNoUpstreamNames(html, "the dashboard HTML");
    const title = html.match(/<title>([^<]*)<\/title>/)?.[1];
    pass(`dashboard ${res.url} -> 200, title "${title}", says FreeBuild, no Openship/OpenShip/Oblien`);
  }
  await checkDashboardBind(dashPort);
  {
    const res = await fetch(`${dashUrl}/onboarding`);
    const html = await res.text();
    assert.ok(res.status < 500, `GET /onboarding (after redirects, ${res.url}) answered ${res.status}`);
    assertNoUpstreamNames(html, `the /onboarding HTML (${res.url})`);
    const title = html.match(/<title>([^<]*)<\/title>/)?.[1];
    pass(`dashboard /onboarding -> ${res.status} at ${res.url}, title "${title}", no Openship/OpenShip/Oblien`);
  }
  // Without a session cookie the proxy sends unknown pages to /login, so the
  // 404 page is fetched the two ways a visitor reaches it: with a session
  // cookie (any value; the proxy checks only its presence), and on a dotted
  // path the proxy matcher skips.
  for (const [what, path, headers] of [
    ["unknown path, session cookie", `/e2e-no-such-page-${randomBytes(4).toString("hex")}`, { cookie: "e2e.session_token=x" }],
    ["unknown dotted path, no cookie", `/e2e-no-such-file-${randomBytes(4).toString("hex")}.txt`, {}],
  ]) {
    const res = await fetch(`${dashUrl}${path}`, { headers, redirect: "manual" });
    const html = await res.text();
    assert.equal(res.status, 404, `${what}: ${path} answered ${res.status} (${res.headers.get("location") ?? ""})`);
    assertNoUpstreamNames(html, `the dashboard 404 HTML (${what})`);
    const title = html.match(/<title>([^<]*)<\/title>/)?.[1];
    pass(`dashboard ${what} -> 404, title "${title}", no Openship/OpenShip/Oblien`);
  }
  await stopProcess("freebuild-dashboard");
}

/** Local addresses of LISTEN sockets on `port`, from /proc/net/tcp and tcp6. Null off Linux. */
function listeningAddresses(port) {
  const out = [];
  let readAny = false;
  for (const file of ["/proc/net/tcp", "/proc/net/tcp6"]) {
    let text;
    try {
      text = readFileSync(file, "utf8");
      readAny = true;
    } catch {
      continue;
    }
    for (const line of text.split("\n").slice(1)) {
      const cols = line.trim().split(/\s+/);
      if (cols.length < 4 || cols[3] !== "0A") continue; // 0A = TCP_LISTEN
      const [hexAddr, hexPort] = cols[1].split(":");
      if (parseInt(hexPort, 16) !== port) continue;
      out.push(decodeProcAddr(hexAddr));
    }
  }
  return readAny ? out : null;
}

/** /proc/net addresses are 32-bit words in host (little-endian) byte order. */
function decodeProcAddr(hex) {
  const words = hex.match(/.{8}/g).map((w) => w.match(/../g).reverse().join(""));
  if (words.length === 1) return words[0].match(/../g).map((b) => parseInt(b, 16)).join(".");
  const v6 = words.join("");
  if (/^0{20}ffff/.test(v6)) return v6.slice(24).match(/../g).map((b) => parseInt(b, 16)).join(".");
  if (/^0+$/.test(v6)) return "::";
  if (/^0{31}1$/.test(v6)) return "::1";
  return v6.match(/.{4}/g).join(":");
}

function assertNoUpstreamNames(html, what) {
  const upstream = html.match(/openship|oblien/i);
  assert.ok(
    !upstream,
    `${what} contains "${upstream?.[0]}" near: ${upstream ? html.slice(Math.max(0, upstream.index - 80), upstream.index + 80) : ""}`,
  );
}

/** The dashboard listens on loopback only when no host is configured. */
async function checkDashboardBind(port) {
  // What the kernel reports for the listening socket (Linux /proc/net).
  const locals = listeningAddresses(port);
  if (locals === null) {
    log("/proc/net/tcp not readable; checking by connecting only");
  } else {
    assert.ok(locals.length > 0, `no listener on :${port} in /proc/net/tcp{,6}`);
    assert.deepEqual([...new Set(locals)], ["127.0.0.1"], `dashboard listens on: ${locals.join(", ")}`);
    pass(`dashboard listens on 127.0.0.1:${port} only (/proc/net/tcp and tcp6), FREEBUILD_DASHBOARD_HOST unset`);
  }
  // And from the outside: a non-loopback address of this host must refuse.
  const external = Object.values(networkInterfaces())
    .flat()
    .find((a) => a && a.family === "IPv4" && !a.internal);
  if (!external) {
    log("no non-loopback IPv4 address on this host; skipping the connect check");
    return;
  }
  let outcome;
  try {
    const res = await fetch(`http://${external.address}:${port}/`, { redirect: "manual", signal: AbortSignal.timeout(5_000) });
    outcome = `HTTP ${res.status}`;
  } catch (err) {
    outcome = String(err?.cause?.code ?? err?.name ?? err);
  }
  assert.ok(!outcome.startsWith("HTTP"), `dashboard answered on ${external.address}:${port} (${outcome})`);
  pass(`dashboard refuses ${external.address}:${port} (${outcome})`);
}

function readAudit(path) {
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

function countFiles(dir) {
  let n = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) n += countFiles(join(dir, entry.name));
    else n++;
  }
  return n;
}

async function cleanup() {
  for (const name of [...children.keys()]) await stopProcess(name);
  cfStub?.closeAllConnections();
  cfStub?.close();
  if (tmpRoot) await rm(tmpRoot, { recursive: true, force: true }).catch(() => {});
}

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    killAllSync();
    if (tmpRoot) spawnSync("rm", ["-rf", tmpRoot], { timeout: 30_000 });
    process.exit(130);
  });
}
process.on("exit", killAllSync);

const started = Date.now();
let failed = false;
try {
  await main();
} catch (err) {
  failed = true;
  console.error(`[e2e] FAIL ${err?.stack ?? err}`);
  for (const child of children.values()) {
    console.error(`[e2e] --- last lines of ${child.logPath} ---\n${tail(child.logPath)}`);
  }
} finally {
  await cleanup();
}
const secs = ((Date.now() - started) / 1000).toFixed(1);
if (failed) {
  console.error(`[e2e] FAILED after ${secs}s (processes stopped, temp dir removed)`);
  process.exit(1);
}
console.log(`[e2e] ALL PASSED in ${secs}s (processes stopped, temp dir removed)`);
