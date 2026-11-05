// Copyright (c) 2026 Netie AI. MIT.
//
// Fast unit test for open-sse/netie/policy.ts — runnable without a build.
// Run: node --import tsx open-sse/netie/__tests__/policy.test.mjs
// (wired as `npm run test:policy`, part of `npm test`.)

import { test, after } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  DISABLED,
  assertProviderAllowed,
  assertServiceToolAllowed,
  classifyProvider,
  classifyProviderId,
  isProviderHidden,
  isServiceToolDisabled,
  NetieDisabledError,
  isNetieDisabledError,
  disabledErrorBody,
  resolveCanonicalProviderId,
  UPSTREAM_UPDATES_DISABLED_MESSAGE,
} from "../policy.ts";
import {
  matchHardDisabledRoute,
  normalizeRoutePath,
  INVALID_PATH_ENCODING_CODE,
} from "../../../src/lib/netie/hardDisabledRoutes.ts";

test("apikey/none/optional providers stay allowed", () => {
  assert.equal(
    classifyProvider({ id: "openai", authType: "apikey", authHeader: "bearer", executor: "default" }),
    null
  );
  assert.equal(
    classifyProvider({ id: "anthropic", authType: "apikey", authHeader: "x-api-key", executor: "default" }),
    null
  );
  assert.equal(
    classifyProvider({ id: "duckduckgo-anon", authType: "none", authHeader: "none", executor: "default" }),
    null
  );
});

test("oauth providers are consumer_subscription_pooling_disabled", () => {
  assert.equal(
    classifyProvider({ id: "codex", authType: "oauth", authHeader: "bearer", executor: "codex" }),
    DISABLED.consumerSubscription
  );
  assert.equal(
    classifyProvider({ id: "cursor", authType: "oauth", authHeader: "bearer", executor: "cursor" }),
    DISABLED.consumerSubscription
  );
});

test("cookie-auth providers are consumer_session_relay_disabled", () => {
  assert.equal(
    classifyProvider({ id: "chatgpt-web", authType: "apikey", authHeader: "cookie", executor: "chatgpt-web" }),
    DISABLED.sessionRelay
  );
});

test("-web suffixed ids are session-relay even with a non-cookie authHeader", () => {
  assert.equal(
    classifyProvider({ id: "deepseek-web", authType: "apikey", authHeader: "bearer", executor: "deepseek-web" }),
    DISABLED.sessionRelay
  );
  assert.equal(
    classifyProvider({ id: "duckduckgo-web", authType: "none", authHeader: "none", executor: "duckduckgo-web" }),
    DISABLED.sessionRelay
  );
});

test("classifyProviderId resolves real registry ids and aliases", () => {
  assert.equal(classifyProviderId("cursor"), DISABLED.consumerSubscription);
  assert.equal(classifyProviderId("codex"), DISABLED.consumerSubscription);
  assert.equal(classifyProviderId("antigravity"), DISABLED.consumerSubscription);
  assert.equal(classifyProviderId("agy"), DISABLED.consumerSubscription); // registry alias for antigravity
  assert.equal(classifyProviderId("claude-web"), DISABLED.sessionRelay);
  assert.equal(classifyProviderId("cw-web"), DISABLED.sessionRelay); // orphan executor alias
  assert.equal(classifyProviderId("grok-web"), DISABLED.sessionRelay);
  assert.equal(classifyProviderId("huggingchat"), DISABLED.sessionRelay); // cookie, no -web suffix
  assert.equal(classifyProviderId("promptql"), DISABLED.sessionRelay); // WEB_COOKIE_PROVIDERS-only
  assert.equal(classifyProviderId("anthropic"), null);
  assert.equal(classifyProviderId("openai"), null);
  assert.equal(classifyProviderId("groq"), null);
});

test("orphan executor aliases with no registry row are still classified", () => {
  assert.equal(classifyProviderId("poe-web"), DISABLED.sessionRelay);
  assert.equal(classifyProviderId("venice-web"), DISABLED.sessionRelay);
  assert.equal(classifyProviderId("v0-vercel-web"), DISABLED.sessionRelay);
  assert.equal(classifyProviderId("copilot"), DISABLED.sessionRelay);
  assert.equal(classifyProviderId("dario"), DISABLED.consumerSubscription);
  assert.equal(classifyProviderId("cliproxyapi"), DISABLED.consumerSubscription);
  assert.equal(classifyProviderId("gitlab"), DISABLED.consumerSubscription);
});

test("unknown ids stay allowed (null)", () => {
  assert.equal(classifyProviderId("not-a-real-provider"), null);
  assert.equal(classifyProviderId(""), null);
  assert.equal(classifyProviderId(null), null);
});

test("isProviderHidden mirrors classification for both entries and ids", () => {
  assert.equal(isProviderHidden("cursor"), true);
  assert.equal(isProviderHidden("openai"), false);
  assert.equal(
    isProviderHidden({ id: "grok-web", authType: "apikey", authHeader: "cookie", executor: "grok-web" }),
    true
  );
});

test("NetieDisabledError carries the exact 501 shape", () => {
  const err = new NetieDisabledError(DISABLED.consumerSubscription);
  assert.equal(err.status, 501);
  assert.equal(err.code, DISABLED.consumerSubscription);
  assert.equal(isNetieDisabledError(err), true);
  assert.equal(isNetieDisabledError(new Error("nope")), false);

  const body = disabledErrorBody(DISABLED.sessionRelay);
  assert.equal(body.error.code, DISABLED.sessionRelay);
  assert.equal(typeof body.error.message, "string");
  assert.ok(body.error.message.length > 0);
});

test("local-CLI executors that spend a human's own subscription login are disabled", () => {
  // Their registry rows say authType "none", so they are classified by id,
  // alias and executor before the registry lookup.
  for (const id of ["devin-cli-agentic", "dva", "auggie", "aug", "zcode", "zc", "codex-app-server", "cxa"]) {
    assert.equal(classifyProviderId(id), DISABLED.consumerSubscription, id);
  }
  assert.equal(
    classifyProvider({ id: "auggie", authType: "none", authHeader: "none", executor: "auggie" }),
    DISABLED.consumerSubscription
  );
});

test("cloudflare-playground (browser-grade TLS fingerprint) is tls_fingerprint_stealth_disabled", () => {
  assert.equal(classifyProviderId("cloudflare-playground"), DISABLED.tlsStealth);
  assert.equal(classifyProviderId("cfp"), DISABLED.tlsStealth);
  assert.equal(
    classifyProvider({
      id: "cloudflare-playground",
      authType: "none",
      authHeader: "none",
      executor: "cloudflare-playground",
    }),
    DISABLED.tlsStealth
  );
});

test("the upstream-updates 501 message does not name the upstream product", () => {
  assert.doesNotMatch(UPSTREAM_UPDATES_DISABLED_MESSAGE, /omniroute|9router/i);
});

test("hard-disabled route table covers /authorize and per-connection login/refresh routes", () => {
  const cases = [
    ["/authorize", "consumer_subscription_pooling_disabled"],
    ["/AUTHORIZE", "consumer_subscription_pooling_disabled"],
    ["/api/providers/abc123/login", "consumer_session_relay_disabled"],
    ["/api/providers/abc123/refresh-token", "consumer_session_relay_disabled"],
    ["/api/providers/abc123/refresh", "consumer_subscription_pooling_disabled"],
    ["/api/providers/abc123/refresh-cursor", "consumer_subscription_pooling_disabled"],
    ["/api/providers/abc123/codex-auth/apply-local", "consumer_subscription_pooling_disabled"],
    ["/api/providers/command-code/auth/apply", "keys_managed_by_openvault"],
    ["/api/providers/command-code/auth/callback", "keys_managed_by_openvault"],
    ["/api/providers/volcengine-plan/connect", "consumer_session_relay_disabled"],
  ];
  for (const [path, code] of cases) {
    const match = matchHardDisabledRoute(path);
    assert.ok(match, path);
    assert.equal(match.status, 501, path);
    assert.equal(match.body.error.code, code, path);
  }
  for (const path of ["/api/providers", "/api/providers/abc123", "/api/providers/abc123/test", "/authorized"]) {
    assert.equal(matchHardDisabledRoute(path), null, path);
  }
});

// Security: Next.js decodes the path before it picks a handler, so a table that
// only reads the raw string is bypassed by an escaped spelling of the same route.
test("hard-disabled routes cannot be reached through an escaped, doubled or dotted spelling", () => {
  const oauth = "consumer_subscription_pooling_disabled";
  const relay = "consumer_session_relay_disabled";
  const cases = [
    ["/api/%6fauth/codex/authorize", oauth],
    ["/api/%6Fauth/codex/authorize", oauth],
    ["/api/o%61uth/codex/authorize", oauth],
    ["/api/oauth%2fcodex/authorize", oauth],
    ["/api/%256fauth/codex/authorize", oauth], // %25 -> %, then %6f -> o
    ["/api/%25256fauth/codex/authorize", oauth], // three layers
    ["/%61pi/oauth/codex/authorize", oauth],
    ["/API/%4Fauth/codex/authorize", oauth],
    ["/api//oauth/codex/authorize", oauth],
    ["/api/./oauth/codex/authorize", oauth],
    ["/api/x/../oauth/codex/authorize", oauth],
    ["/api\\oauth\\codex\\authorize", oauth],
    ["/api/oauth/", oauth],
    ["/api/tools/%61gent-bridge/status", relay],
    ["/api/tools/agent%2Dbridge/status", relay],
    ["/api/settings/m%69tm", relay],
    ["/api/services/%64ario/install", oauth],
    ["/api/services/dari%6f/admin/login-start", oauth],
    ["/api/services/%63liproxy/accounts", oauth],
    ["/api/version-%6danager/start", oauth],
    ["/api/providers/abc123/%6cogin", relay],
    ["/api/providers/abc123/refresh%2dtoken", relay],
    ["/api/providers/%63laude-auth/import", oauth],
    ["/api/co%70ilot/chat", relay],
    ["/dashboard/providers/services/%64ario/embed/", oauth],
  ];
  for (const [path, code] of cases) {
    const match = matchHardDisabledRoute(path);
    assert.ok(match, `${path} must be refused`);
    assert.equal(match.status, 501, path);
    assert.equal(match.body.error.code, code, path);
  }
});

test("a path whose encoding cannot be read answers 400 invalid_path_encoding, not a guess", () => {
  const unreadable = [
    "/api/%zz/x",
    "/api/oauth/%",
    "/api/oauth/%E0%A4%A", // truncated UTF-8 sequence
    "/api/a%00b",
    "/api/%2500/x", // %00 behind one layer
    "/api/%252525256fauth/x", // more layers than the decoder will unwrap
  ];
  for (const path of unreadable) {
    const match = matchHardDisabledRoute(path);
    assert.ok(match, path);
    assert.equal(match.status, 400, path);
    assert.equal(match.body.error.code, INVALID_PATH_ENCODING_CODE, path);
    assert.equal(normalizeRoutePath(path), null, path);
  }
});

test("normalizing the path leaves ordinary routes alone", () => {
  const open = [
    "/",
    "/api/providers",
    "/api/providers/abc123",
    "/api/providers/a%20b",
    "/api/providers/abc123/test",
    "/api/oauthx/codex",
    "/api/v1/models",
    "/v1/chat/completions",
    "/v1/models/gpt%2D4o",
    "/v1beta/models/gemini-pro:generateContent",
    "/dashboard/providers",
    "/api/services/mux/status",
    "/api/services/darion/status",
  ];
  for (const path of open) {
    assert.equal(matchHardDisabledRoute(path), null, path);
  }
  assert.equal(normalizeRoutePath("/API//Foo/./Bar/../Baz/"), "/api/foo/baz");
  assert.equal(normalizeRoutePath("/a%2Fb"), "/a/b");
});

// Security: the keyless OpenCode free tier used to get a forged OpenCode client
// identity (User-Agent opencode/1.18.x, x-opencode-client, ses_/msg_ ids) so a
// datacenter caller passed a client-only check. FreeRoute adds none of it.
test("OpenCode requests carry no synthesized client identity", async () => {
  const { forwardOpencodeClientHeaders } = await import("../../utils/opencodeHeaders.ts");

  // A generic client: nothing is invented for it.
  const generic = {};
  forwardOpencodeClientHeaders(generic, { "user-agent": "curl/8.5.0" }, {});
  assert.deepEqual(generic["user-agent"] ?? generic["User-Agent"], "curl/8.5.0");
  for (const name of ["x-opencode-client", "x-opencode-project", "x-opencode-session", "x-opencode-request"]) {
    assert.equal(generic[name], undefined, `${name} must not be synthesized`);
  }

  // No client headers at all: still nothing.
  const bare = {};
  forwardOpencodeClientHeaders(bare, {}, {});
  assert.deepEqual(bare, {});

  // A real OpenCode client's own headers pass through untouched.
  const real = {};
  const own = {
    "user-agent": "opencode/1.99.0",
    "x-opencode-client": "cli",
    "x-opencode-session": "ses_0123456789abAbCdEfGhIjKlMn",
  };
  forwardOpencodeClientHeaders(real, own, {});
  assert.equal(real["x-opencode-client"], "cli");
  assert.equal(real["x-opencode-session"], own["x-opencode-session"]);

  // The sources hold no fabricated identity either.
  const { readFileSync } = await import("node:fs");
  for (const file of ["utils/opencodeHeaders.ts", "executors/opencode.ts"]) {
    const source = readFileSync(new URL(`../../${file}`, import.meta.url), "utf8");
    assert.doesNotMatch(source, /opencode\/1\.\d+\.\d+/, file);
    assert.doesNotMatch(source, /resolveOpencodeCliDefaults|applyCliDefaults|canonicalId/, file);
  }
  const example = readFileSync(new URL("../../../.env.example", import.meta.url), "utf8");
  assert.doesNotMatch(example, /^#?OPENCODE_(SYNTHESIZE_CLI_HEADERS|USER_AGENT|CLIENT|PROJECT)=/m);
});

// ── Round 3 regression tests ────────────────────────────────────────────────
// The modules below open FreeRoute's SQLite store when they load, so the data
// directory is a throwaway one. Everything is imported inside the tests, after
// DATA_DIR is set.

const ROUND3_DATA_DIR = mkdtempSync(join(tmpdir(), "freeroute-policy-"));
process.env.DATA_DIR = ROUND3_DATA_DIR;
process.env.DISABLE_SQLITE_AUTO_BACKUP = "true";
after(() => {
  rmSync(ROUND3_DATA_DIR, { recursive: true, force: true });
});

const SRC = new URL("../../../src/", import.meta.url).pathname;
const OPEN_SSE = new URL("../../", import.meta.url).pathname;

// Item 1: the Dario and CLIProxyAPI sidecars (Claude Pro/Max subscription pooling).

test("the Dario and CLIProxyAPI sidecar families and the version manager answer 501 consumer_subscription_pooling_disabled", () => {
  const disabled = [
    "/api/services/dario/install",
    "/api/services/dario/start",
    "/api/services/dario/stop",
    "/api/services/dario/restart",
    "/api/services/dario/update",
    "/api/services/dario/status",
    "/api/services/dario/auto-start",
    "/api/services/dario/auto-restart-adopted",
    "/api/services/dario/admin/login-start",
    "/api/services/dario/admin/login-complete",
    "/api/services/dario/admin/accounts",
    "/api/services/dario/admin/import-from-omniroute",
    "/API/Services/Dario/Install",
    "/api/services/cliproxy/install",
    "/api/services/cliproxy/start",
    "/api/services/cliproxy/stop",
    "/api/services/cliproxy/restart",
    "/api/services/cliproxy/update",
    "/api/services/cliproxy/status",
    "/api/services/cliproxy/auto-start",
    "/api/services/cliproxy/accounts",
    "/api/services/cliproxy/provider-expose",
    "/api/version-manager/install",
    "/api/version-manager/start",
    "/api/version-manager/stop",
    "/api/version-manager/restart",
    "/api/version-manager/status",
    "/api/version-manager/check-update",
    "/dashboard/providers/services/dario/embed/",
    "/dashboard/providers/services/cliproxy/embed/anything",
  ];
  for (const path of disabled) {
    const match = matchHardDisabledRoute(path);
    assert.ok(match, path);
    assert.equal(match.status, 501, path);
    assert.equal(match.body.error.code, "consumer_subscription_pooling_disabled", path);
    assert.doesNotMatch(match.body.error.message, /omniroute|9router|openship|oblien/i, path);
  }
  assert.equal(
    matchHardDisabledRoute("/api/services/9router/install").body.error.code,
    "upstream_service_not_included"
  );
  for (const path of ["/api/services/mux/status", "/api/services/bifrost/status", "/api/services/darion/status"]) {
    assert.equal(matchHardDisabledRoute(path), null, path);
  }
});

test("assertServiceToolAllowed refuses dario, cliproxy, cliproxyapi, openwa and 9router and nothing else", () => {
  for (const tool of ["dario", "cliproxy", "cliproxyapi", " Dario ", "CLIPROXY"]) {
    assert.equal(isServiceToolDisabled(tool), true, tool);
    assert.throws(
      () => assertServiceToolAllowed(tool),
      (error) => isNetieDisabledError(error) && error.code === DISABLED.consumerSubscription,
      tool
    );
  }
  for (const tool of ["openwa", " OpenWa "]) {
    assert.equal(isServiceToolDisabled(tool), true, tool);
    assert.throws(
      () => assertServiceToolAllowed(tool),
      (error) => isNetieDisabledError(error) && error.code === DISABLED.sessionRelay,
      tool
    );
  }
  for (const tool of ["9router", " 9ROUTER "]) {
    assert.equal(isServiceToolDisabled(tool), true, tool);
    assert.throws(
      () => assertServiceToolAllowed(tool),
      (error) =>
        isNetieDisabledError(error) &&
        error.code === "upstream_service_not_included" &&
        error.status === 501 &&
        !/omniroute|9router|openship|oblien/i.test(error.message),
      tool
    );
  }
  for (const tool of ["mux", "bifrost", "", null, undefined]) {
    assert.equal(isServiceToolDisabled(tool), false, String(tool));
    assertServiceToolAllowed(tool);
  }
});

async function assertDisabledRejection(promiseOrFn, label) {
  await assert.rejects(
    async () => (typeof promiseOrFn === "function" ? promiseOrFn() : promiseOrFn),
    (error) => isNetieDisabledError(error) && error.code === DISABLED.consumerSubscription,
    label
  );
}

test("no code path can supervise, install or spawn the Dario or CLIProxyAPI sidecar", async () => {
  const { ServiceSupervisor } = await import(`${SRC}lib/services/ServiceSupervisor.ts`);
  const config = (tool) => ({
    tool,
    port: 1,
    spawnArgs: () => ({ command: process.execPath, args: ["-e", "0"], env: {}, cwd: "." }),
    healthUrl: () => "http://127.0.0.1:1/health",
    healthIntervalMs: 60_000,
    stopTimeoutMs: 1,
    logsBufferBytes: 1024,
  });
  for (const tool of ["dario", "cliproxy"]) {
    assert.throws(
      () => new ServiceSupervisor(config(tool)),
      (error) => isNetieDisabledError(error) && error.code === DISABLED.consumerSubscription,
      tool
    );
  }
  // Control: the gate is about these two sidecars only.
  assert.doesNotThrow(() => new ServiceSupervisor(config("mux")));

  const dario = await import(`${SRC}lib/services/installers/dario.ts`);
  const cliproxy = await import(`${SRC}lib/services/installers/cliproxy.ts`);
  await assertDisabledRejection(() => dario.install(), "dario install");
  await assertDisabledRejection(() => dario.update(), "dario update");
  await assertDisabledRejection(() => dario.resolveSpawnArgs("k", 3456), "dario spawn args");
  await assertDisabledRejection(() => cliproxy.install(), "cliproxy install");
  await assertDisabledRejection(() => cliproxy.update(), "cliproxy update");
  await assertDisabledRejection(() => cliproxy.resolveSpawnArgs(8317), "cliproxy spawn args");

  const versionManager = await import(`${SRC}lib/versionManager/index.ts`);
  for (const tool of ["cliproxy", "cliproxyapi", "dario"]) {
    await assertDisabledRejection(() => versionManager.installTool(tool), `installTool ${tool}`);
    await assertDisabledRejection(() => versionManager.startTool(tool), `startTool ${tool}`);
    await assertDisabledRejection(() => versionManager.restartTool(tool), `restartTool ${tool}`);
    await assertDisabledRejection(() => versionManager.checkForUpdates(tool), `checkForUpdates ${tool}`);
  }
  const processManager = await import(`${SRC}lib/versionManager/processManager.ts`);
  await assertDisabledRejection(() => processManager.startProcess("/bin/true"), "startProcess");
  const binaryManager = await import(`${SRC}lib/versionManager/binaryManager.ts`);
  await assertDisabledRejection(() => binaryManager.installVersion("0.0.0"), "installVersion");
});

test("the startup bootstrap never supervises or auto-starts dario, cliproxy, open-wa or 9router, whatever the stored row says", async () => {
  const { upsertVersionManagerTool } = await import(`${SRC}lib/db/versionManager.ts`);
  const seededPorts = { dario: 3456, cliproxy: 8317, openwa: 8323, "9router": 20130 };
  for (const [tool, port] of Object.entries(seededPorts)) {
    await upsertVersionManagerTool({
      tool,
      installedVersion: "1.0.0",
      status: "stopped",
      autoStart: true,
      port,
    });
  }
  // Control: an ordinary installed service is still supervised, so the bootstrap ran.
  await upsertVersionManagerTool({
    tool: "mux",
    installedVersion: "1.0.0",
    status: "stopped",
    autoStart: false,
    port: 3457,
  });
  const { bootstrapEmbeddedServices } = await import(`${SRC}lib/services/bootstrap.ts`);
  const { getSupervisor } = await import(`${SRC}lib/services/registry.ts`);
  await bootstrapEmbeddedServices();
  assert.ok(getSupervisor("mux"), "the bootstrap must still supervise an installed mux");
  assert.equal(getSupervisor("dario") ?? null, null, "dario must not be supervised");
  assert.equal(getSupervisor("cliproxy") ?? null, null, "cliproxy must not be supervised");
  assert.equal(getSupervisor("openwa") ?? null, null, "open-wa must not be supervised");
  assert.equal(getSupervisor("9router") ?? null, null, "9router must not be supervised");
});

test("setting an upstream proxy mode that routes through a sidecar answers 501", async () => {
  const route = await import(`${SRC}app/api/upstream-proxy/[providerId]/route.ts`);
  for (const body of [
    { mode: "cliproxyapi" },
    { mode: "dario" },
    { mode: "fallback" },
    { mode: "fallback", fallbackBackend: "dario" },
  ]) {
    const res = await route.PUT(
      new Request("http://127.0.0.1/api/upstream-proxy/claude", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
      { params: Promise.resolve({ providerId: "claude" }) }
    );
    assert.equal(res.status, 501, JSON.stringify(body));
    assert.equal((await res.json()).error.code, "consumer_subscription_pooling_disabled");
  }
});

// Item 2: no rotating browser fingerprints on keyless Pollinations image requests.

test("a keyless Pollinations image request gets the plain headers and no fingerprint session", async () => {
  const mod = await import(`${OPEN_SSE}handlers/imageGeneration/pollinationsAnonAuth.ts`);
  const plain = { "Content-Type": "application/json" };
  for (const token of [undefined, "", "pk-real"]) {
    const out = await mod.applyPollinationsAnonymousFallback("pollinations", token, { ...plain });
    assert.deepEqual(out.headers, plain);
    assert.equal(out.session, null);
    const names = Object.keys(out.headers).map((name) => name.toLowerCase());
    for (const fingerprint of ["user-agent", "accept-language", "sec-ch-ua", "sec-ch-ua-platform", "sec-ch-ua-mobile"]) {
      assert.ok(!names.includes(fingerprint), `${fingerprint} must not be added`);
    }
  }
  assert.doesNotThrow(() => mod.reportPollinationsAnonOutcome(null, 429));
  const source = readFileSync(`${OPEN_SSE}handlers/imageGeneration/pollinationsAnonAuth.ts`, "utf8");
  assert.doesNotMatch(source, /new SessionPool|sessionPool\//, "the image path must not build a session pool");
});

// Item 3: every alias classifies like its canonical id.

test("firefly, aq and poe classify like adobe-firefly, amazon-q and poe-web", () => {
  for (const [alias, canonical] of [
    ["firefly", "adobe-firefly"],
    ["Firefly", "adobe-firefly"],
    [" firefly ", "adobe-firefly"],
    ["aq", "amazon-q"],
    ["poe", "poe-web"],
  ]) {
    assert.notEqual(classifyProviderId(canonical), null, canonical);
    assert.equal(classifyProviderId(alias), classifyProviderId(canonical), alias);
    assert.equal(resolveCanonicalProviderId(alias), canonical, alias);
  }
  assert.equal(classifyProviderId("firefly"), DISABLED.sessionRelay);
  assert.equal(resolveCanonicalProviderId("openai"), "openai");
  assert.equal(resolveCanonicalProviderId("not-a-real-provider"), "not-a-real-provider");
  // `v0` is the API-key provider v0-vercel in the registry, so the spelling stays allowed;
  // dispatch is what refuses the browser-session executor the executor map hangs on it.
  assert.equal(classifyProviderId("v0"), null);
  assert.equal(classifyProviderId("v0-vercel"), null);
  assert.equal(classifyProviderId("v0-vercel-web"), DISABLED.sessionRelay);
});

test("every registry, catalog and media-registry alias classifies like its canonical id", async () => {
  const pairs = [];
  const collect = (source, entries) => {
    for (const [key, entry] of entries) {
      const id = entry?.id ?? key;
      if (entry?.alias && entry.alias !== id) pairs.push({ source, alias: entry.alias, id });
    }
  };
  const providerRegistry = await import(`${OPEN_SSE}config/providerRegistry.ts`);
  collect("REGISTRY", Object.entries(providerRegistry.REGISTRY));
  const catalog = await import(`${SRC}shared/constants/providers.ts`);
  collect("AI_PROVIDERS", Object.entries(catalog.AI_PROVIDERS));
  for (const file of [
    "imageRegistry",
    "videoRegistry",
    "musicRegistry",
    "audioRegistry",
    "embeddingRegistry",
    "rerankRegistry",
    "moderationRegistry",
    "ocrRegistry",
    "searchRegistry",
    "upscaleRegistry",
  ]) {
    const mod = await import(`${OPEN_SSE}config/${file}.ts`);
    for (const [name, value] of Object.entries(mod)) {
      if (/PROVIDERS$/.test(name) && value && typeof value === "object") {
        collect(`${file}.${name}`, Object.entries(value));
      }
    }
  }
  assert.ok(pairs.length > 100, `expected the walk to see 100+ alias pairs, saw ${pairs.length}`);
  const mismatches = pairs.filter(({ alias, id }) => classifyProviderId(alias) !== classifyProviderId(id));
  assert.deepEqual(
    mismatches.map(({ source, alias, id }) => `${source}: ${alias} -> ${id}`),
    []
  );
});

test("no executor alias dispatches to a disabled executor, and each classifies like the provider it serves", async () => {
  const executors = await import(`${OPEN_SSE}executors/index.ts`);
  const registry = await import(`${OPEN_SSE}executors/registry.ts`);
  const aliases = registry.listExecutorAliases();
  assert.ok(aliases.length > 100, `expected 100+ executor aliases, saw ${aliases.length}`);
  const problems = [];
  let walked = 0;
  for (const alias of aliases) {
    let served;
    try {
      served = (await registry.loadRegisteredExecutor(alias))?.getProvider?.();
    } catch {
      continue; // an executor whose optional native dependency is absent cannot be walked
    }
    if (!served) continue;
    walked += 1;

    // Dispatch invariant: getExecutor(alias) either refuses with a named 501 or
    // returns an executor whose provider is allowed.
    try {
      const executor = await executors.getExecutor(alias);
      if (classifyProviderId(executor.getProvider()) !== null) {
        problems.push(`${alias}: dispatched a disabled executor (${executor.getProvider()})`);
      }
    } catch (error) {
      if (!isNetieDisabledError(error)) problems.push(`${alias}: getExecutor threw ${error}`);
    }

    // Classification of the spelling matches the provider it serves, except where the
    // registry gives the same spelling to a different provider (the `v0` shadow).
    const registryMeaning = resolveCanonicalProviderId(alias);
    if (registryMeaning !== served && registryMeaning !== alias) continue;
    if (classifyProviderId(alias) !== classifyProviderId(served)) {
      problems.push(`${alias} -> ${served}: ${classifyProviderId(alias)} vs ${classifyProviderId(served)}`);
    }
  }
  assert.ok(walked > 100, `expected to load 100+ executors, loaded ${walked}`);
  assert.deepEqual(problems, []);
  // The shadowing alias is refused at dispatch even though the spelling is allowed.
  await assert.rejects(
    executors.getExecutor("v0"),
    (error) => isNetieDisabledError(error) && error.code === DISABLED.sessionRelay
  );
});

// Item 4: no Claude Code client impersonation.

test("agentrouter and anthropic-compatible-cc-* send no Claude Code wire image", async () => {
  const builtins = await import(`${OPEN_SSE}services/ccWireImageBuiltins.ts`);
  assert.equal(builtins.CC_WIRE_IMAGE_BUILTINS.size, 0);
  assert.equal(builtins.usesCcWireImage("agentrouter"), false);
  const provider = await import(`${OPEN_SSE}services/provider.ts`);
  const ccCompat = await import(`${OPEN_SSE}services/claudeCodeCompatible.ts`);
  for (const id of ["agentrouter", "anthropic-compatible-cc-0b6a1f", "claude"]) {
    assert.equal(provider.isClaudeCodeCompatible(id), false, id);
    assert.equal(ccCompat.isClaudeCodeCompatibleProvider(id), false, id);
  }

  // Plain API-key call: the registry's own x-api-key and honest headers.
  const headers = provider.buildProviderHeaders("agentrouter", { apiKey: "key-123" }, false);
  const names = Object.keys(headers).map((name) => name.toLowerCase());
  assert.equal(headers["x-api-key"] ?? headers["X-Api-Key"], "key-123");
  for (const name of names) {
    assert.doesNotMatch(name, /^x-stainless-|^x-app$|^x-claude-code|dangerous-direct-browser/);
  }
  assert.doesNotMatch(String(headers["User-Agent"] ?? headers["user-agent"] ?? ""), /claude-cli|claude-code/i);
});

test("anthropic-compatible-cc-* nodes answer tls_fingerprint_stealth_disabled, plain anthropic-compatible nodes stay allowed", () => {
  assert.equal(classifyProviderId("anthropic-compatible-cc-0b6a1f"), DISABLED.tlsStealth);
  assert.equal(
    classifyProvider({ id: "anthropic-compatible-cc-0b6a1f", authType: "apikey", authHeader: "bearer" }),
    DISABLED.tlsStealth
  );
  assert.throws(
    () => assertProviderAllowed("anthropic-compatible-cc-0b6a1f"),
    (error) => isNetieDisabledError(error) && error.code === "tls_fingerprint_stealth_disabled"
  );
  assert.equal(classifyProviderId("anthropic-compatible-0b6a1f"), null);
  assert.equal(classifyProviderId("agentrouter"), null);
});

test("header and body re-ordering to look like a client binary is off, whatever the settings say", async () => {
  const fingerprints = await import(`${OPEN_SSE}config/cliFingerprints.ts`);
  const saved = { all: process.env.CLI_COMPAT_ALL, claude: process.env.CLI_COMPAT_CLAUDE };
  process.env.CLI_COMPAT_ALL = "1";
  process.env.CLI_COMPAT_CLAUDE = "1";
  try {
    fingerprints.setCliCompatProviders(["claude", "codex", "github", "antigravity"]);
    for (const provider of ["claude", "codex", "github", "antigravity", "anthropic-compatible-cc-x"]) {
      assert.equal(fingerprints.isCliCompatEnabled(provider), false, provider);
      const headers = { Host: "h", "X-Custom": "1", "Content-Type": "application/json", "User-Agent": "honest/1.0" };
      const out = fingerprints.applyFingerprint(provider, { ...headers }, { stream: true, model: "m", messages: [] });
      assert.deepEqual(Object.keys(out.headers), Object.keys(headers), provider);
      assert.equal(out.headers["User-Agent"], "honest/1.0", provider);
      assert.deepEqual(Object.keys(JSON.parse(out.bodyString)), ["stream", "model", "messages"], provider);
    }
  } finally {
    fingerprints.setCliCompatProviders([]);
    for (const [name, value] of [["CLI_COMPAT_ALL", saved.all], ["CLI_COMPAT_CLAUDE", saved.claude]]) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

// ── Round 4: consumer paths (CLI emulation and impersonation) ───────────────

// Item 2: the GLM Anthropic transport sends honest headers only.

test("the GLM Anthropic transport sends no Claude Code client identity", async () => {
  const { GlmExecutor } = await import(`${OPEN_SSE}executors/glm.ts`);
  const glmProvider = await import(`${OPEN_SSE}config/glmProvider.ts`);
  const executor = new GlmExecutor("glm");
  const allowed = new Set(["content-type", "accept", "x-api-key", "anthropic-version", "anthropic-beta"]);
  const forbidden =
    /^x-stainless-|^x-claude-code-|^x-client-request-id$|^x-app$|^user-agent$|dangerous-direct-browser-access/i;
  const variants = [
    [{ apiKey: "sk-test", providerSpecificData: { primaryTransport: "anthropic" } }, true],
    [{ apiKey: "sk-test", providerSpecificData: { baseUrl: "https://api.z.ai/api/anthropic" } }, false],
  ];
  for (const [credentials, stream] of variants) {
    const headers = executor.buildHeaders(credentials, stream);
    assert.equal(headers["x-api-key"], "sk-test");
    assert.equal(headers["anthropic-version"], "2023-06-01");
    for (const name of Object.keys(headers)) {
      assert.ok(allowed.has(name.toLowerCase()), `unexpected GLM Anthropic header ${name}`);
      assert.doesNotMatch(name, forbidden, name);
    }
    assert.doesNotMatch(headers["anthropic-beta"], /claude-code/i);
  }
  const direct = glmProvider.buildGlmBaseHeaders("sk-test", true);
  assert.deepEqual(Object.keys(direct).map((name) => name.toLowerCase()).sort(), [...allowed].sort());
  assert.doesNotMatch(glmProvider.GLM_ANTHROPIC_BETA, /claude-code/i);
  for (const file of ["config/glmProvider.ts", "executors/glm.ts"]) {
    const source = readFileSync(`${OPEN_SSE}${file}`, "utf8");
    assert.doesNotMatch(source, /claudeCodeClient|getClaudeCodeUserAgent|X-Claude-Code|X-Stainless/, file);
  }
});

// Items 3 and 4: providers that play another vendor's CLI.

test("freebuff, cursor-api, qoder and muse-code answer a named 501, and are hidden from pickers", async () => {
  const expected = [
    ["freebuff", DISABLED.tlsStealth],
    ["fb", DISABLED.tlsStealth],
    ["Freebuff", DISABLED.tlsStealth],
    ["cursor-api", DISABLED.tlsStealth],
    ["cua", DISABLED.tlsStealth],
    ["muse-code", DISABLED.tlsStealth],
    ["mc", DISABLED.tlsStealth],
    ["qoder", DISABLED.consumerSubscription],
    ["if", DISABLED.consumerSubscription],
  ];
  const executors = await import(`${OPEN_SSE}executors/index.ts`);
  for (const [id, code] of expected) {
    assert.equal(classifyProviderId(id), code, id);
    assert.equal(isProviderHidden(id), true, id);
    assert.throws(
      () => assertProviderAllowed(id),
      (error) => isNetieDisabledError(error) && error.code === code,
      id
    );
  }
  for (const id of ["freebuff", "fb", "cursor-api", "cua", "qoder"]) {
    await assert.rejects(
      executors.getExecutor(id),
      (error) => isNetieDisabledError(error) && error.code === classifyProviderId(id),
      `getExecutor(${id})`
    );
  }
  // Control: the documented API-key providers next to them stay allowed.
  for (const id of ["command-code", "cmd", "glm", "gitlawb", "gitlawb-gmi", "openai"]) {
    assert.equal(classifyProviderId(id), null, id);
  }
  const catalog = await import(`${SRC}lib/providers/catalog.ts`);
  const listed = new Set();
  for (const category of ["apikey", "oauth"]) {
    for (const id of Object.keys(catalog.getStaticProviderCatalogGroup(category).providers)) listed.add(id);
  }
  assert.ok(listed.size > 50, `expected the catalog to list 50+ providers, saw ${listed.size}`);
  for (const id of ["freebuff", "cursor-api", "qoder", "muse-code"]) {
    assert.ok(!listed.has(id), `${id} must not be offered in the Add Connection picker`);
  }
  assert.ok(listed.has("command-code"), "control: command-code stays listed");
});

test("the Cursor Agent CLI build id is never scraped from cursor.com", async () => {
  const version = await import(`${OPEN_SSE}utils/cursorAgentCliVersion.ts`);
  assert.equal(version.refreshCursorAgentCliVersionFromInstaller, undefined);
  assert.equal(version.extractVersionIdFromInstallerScript, undefined);
  const source = readFileSync(`${OPEN_SSE}utils/cursorAgentCliVersion.ts`, "utf8");
  assert.doesNotMatch(source, /cursor\.com\/install|fetchImpl|globalThis\.fetch|\bfetch\(/);
  // Without a local install or env override the pin is returned, with no timer or request queued.
  const saved = { env: process.env.CURSOR_AGENT_CLI_VERSION, home: process.env.HOME };
  const realFetch = globalThis.fetch;
  let fetched = 0;
  globalThis.fetch = (...args) => {
    fetched += 1;
    return realFetch(...args);
  };
  try {
    delete process.env.CURSOR_AGENT_CLI_VERSION;
    process.env.HOME = ROUND3_DATA_DIR;
    version.resetCursorAgentCliVersionCache();
    assert.equal(version.getCursorAgentCliVersion(), version.CURSOR_AGENT_CLI_VERSION);
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(fetched, 0);
  } finally {
    globalThis.fetch = realFetch;
    version.resetCursorAgentCliVersionCache();
    for (const [name, value] of [["CURSOR_AGENT_CLI_VERSION", saved.env], ["HOME", saved.home]]) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

test("Command Code never replays the CLI wire protocol: a 403 from the provider endpoint is the answer", async () => {
  const { CommandCodeExecutor } = await import(`${OPEN_SSE}executors/commandCode.ts`);
  const realFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), headers: { ...(init?.headers ?? {}) } });
    return new Response("denied", { status: 403, headers: { "content-type": "text/plain" } });
  };
  try {
    const result = await new CommandCodeExecutor().execute({
      model: "deepseek/deepseek-v4-flash",
      body: { messages: [{ role: "user", content: "hi" }] },
      stream: false,
      credentials: { apiKey: "cc-key" },
    });
    assert.equal(result.response.status, 403);
  } finally {
    globalThis.fetch = realFetch;
  }
  assert.equal(calls.length, 1, `expected one request, saw ${calls.map((call) => call.url).join(", ")}`);
  assert.match(calls[0].url, /\/provider\/v1\/chat\/completions$/);
  const names = Object.keys(calls[0].headers).map((name) => name.toLowerCase());
  for (const name of names) assert.doesNotMatch(name, /^x-command-code-|^x-cli-environment$|^x-project-slug$/);
  for (const file of [`${OPEN_SSE}executors/commandCode.ts`, `${SRC}lib/providers/validation/openaiFormat.ts`]) {
    const source = readFileSync(file, "utf8");
    assert.doesNotMatch(source, /x-command-code-version|x-cli-environment|executeCliFallback|buildCliUrl/, file);
  }
});

test("Gitlawb sends no OpenClaude CLI identity", async () => {
  const { REGISTRY } = await import(`${OPEN_SSE}config/providerRegistry.ts`);
  for (const id of ["gitlawb", "gitlawb-gmi"]) {
    const names = Object.keys(REGISTRY[id].headers ?? {}).map((name) => name.toLowerCase());
    assert.ok(!names.includes("user-agent") && !names.includes("x-title"), `${id}: ${names}`);
    assert.doesNotMatch(JSON.stringify(REGISTRY[id].headers ?? {}), /openclaude/i, id);
  }
});

// Item 5: the CLI login for a consumer subscription.

test("`freeroute login antigravity` prints the named 501 text and exits non-zero without running a flow", async () => {
  const login = await import("../../../bin/cli/commands/login.mjs");
  const { Command } = await import("commander");
  const program = new Command();
  program.exitOverride();
  login.registerLogin(program);

  const realWrite = process.stderr.write.bind(process.stderr);
  const realExit = process.exit;
  const written = [];
  let exitCode = null;
  process.stderr.write = (text) => {
    written.push(String(text));
    return true;
  };
  process.exit = (code) => {
    exitCode = code;
    return undefined;
  };
  try {
    await program.parseAsync(["node", "freeroute", "login", "antigravity", "--no-browser"]);
  } finally {
    process.stderr.write = realWrite;
    process.exit = realExit;
  }
  assert.equal(exitCode, 1);
  const output = written.join("");
  assert.match(output, /consumer_subscription_pooling_disabled/);
  assert.ok(output.includes(login.LOGIN_DISABLED_MESSAGE));
  assert.doesNotMatch(output, /omniroute|openship|oblien/i);

  const source = readFileSync(new URL("../../../bin/cli/commands/login.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(source, /createServer|accounts\.google\.com|buildAuthUrl|exchangeToken|paste-credentials|import\("open"\)/);
  assert.equal(login.runAntigravityLogin, undefined);
  assert.equal(login.buildAntigravityAuthRequest, undefined);
});

// Items 6 and 8: the open-wa and 9router sidecars.

test("open-wa and 9router cannot be supervised, installed or spawned, and their routes answer a named 501", async () => {
  const { ServiceSupervisor } = await import(`${SRC}lib/services/ServiceSupervisor.ts`);
  const config = (tool) => ({
    tool,
    port: 1,
    spawnArgs: () => ({ command: process.execPath, args: ["-e", "0"], env: {}, cwd: "." }),
    healthUrl: () => "http://127.0.0.1:1/health",
    healthIntervalMs: 60_000,
    stopTimeoutMs: 1,
    logsBufferBytes: 1024,
  });
  const cases = [
    ["openwa", DISABLED.sessionRelay],
    ["9router", "upstream_service_not_included"],
  ];
  for (const [tool, code] of cases) {
    assert.throws(
      () => new ServiceSupervisor(config(tool)),
      (error) => isNetieDisabledError(error) && error.code === code,
      tool
    );
  }
  const refuses = (code) => (error) => isNetieDisabledError(error) && error.code === code;
  const openwa = await import(`${SRC}lib/services/installers/openwa.ts`);
  await assert.rejects(() => openwa.install(), refuses(DISABLED.sessionRelay), "openwa install");
  await assert.rejects(() => openwa.update(), refuses(DISABLED.sessionRelay), "openwa update");
  assert.throws(() => openwa.resolveSpawnArgs("k", 8323), refuses(DISABLED.sessionRelay), "openwa spawn args");
  const ninerouter = await import(`${SRC}lib/services/installers/ninerouter.ts`);
  const notIncluded = refuses("upstream_service_not_included");
  await assert.rejects(() => ninerouter.install(), notIncluded, "9router install");
  await assert.rejects(() => ninerouter.update(), notIncluded, "9router update");
  assert.throws(() => ninerouter.resolveSpawnArgs("k", 20130), notIncluded, "9router spawn args");

  const executors = await import(`${OPEN_SSE}executors/index.ts`);
  for (const alias of ["9router", "nr"]) {
    await assert.rejects(executors.getExecutor(alias), notIncluded, `getExecutor(${alias})`);
  }

  for (const path of [
    "/api/services/openwa/install",
    "/api/services/openwa/start",
    "/api/services/openwa/stop",
    "/api/services/openwa/restart",
    "/api/services/openwa/update",
    "/api/services/openwa/status",
    "/api/services/openwa/auto-start",
    "/api/services/openwa/auto-restart-adopted",
    "/API/Services/OpenWa/Install",
    "/api/%73ervices/openwa/start",
  ]) {
    const match = matchHardDisabledRoute(path);
    assert.ok(match, path);
    assert.equal(match.status, 501, path);
    assert.equal(match.body.error.code, "consumer_session_relay_disabled", path);
  }
  assert.equal(matchHardDisabledRoute("/api/services/openwatch/status"), null);

  const page = readFileSync(`${SRC}app/(dashboard)/dashboard/providers/services/page.tsx`, "utf8");
  assert.doesNotMatch(page, /OpenwaServiceTab|"openwa"/);
  assert.equal(
    existsSync(`${SRC}app/(dashboard)/dashboard/providers/services/tabs/OpenwaServiceTab.tsx`),
    false
  );
});

// Items 7 and 9: client identity presets and the VibeProxy preset.

test("the Client Identity presets are gone from the UI and a CLI identity header is refused", async () => {
  assert.equal(existsSync(`${SRC}shared/constants/clientIdentityProfiles.ts`), false);
  const modal = readFileSync(
    `${SRC}app/(dashboard)/dashboard/providers/components/AddCompatibleProviderModal.tsx`,
    "utf8"
  );
  assert.doesNotMatch(modal, /clientIdentity|ClientIdentity/);
  const messages = JSON.parse(readFileSync(`${SRC}i18n/messages/en.json`, "utf8"));
  assert.doesNotMatch(JSON.stringify(messages), /clientIdentityLabel|clientIdentityHint|Client Identity/);

  const { findClientImpersonationHeaders, rejectClientImpersonationHeaders } = await import("../policy.ts");
  const cliHeaders = [
    { "User-Agent": "claude-cli/2.1.258 (external, cli)", "X-App": "cli" },
    { "User-Agent": "codex_cli_rs/0.1.0", originator: "codex_cli_rs" },
    { "user-agent": "GeminiCLI/0.1.0 (linux; x64)" },
    { "User-Agent": "MuseCodeCLI/0.1.0 (linux; x64)" },
    { "X-Stainless-Lang": "js" },
    { "X-Claude-Code-Session-Id": "abc" },
  ];
  for (const headers of cliHeaders) {
    assert.notDeepEqual(findClientImpersonationHeaders(headers), [], JSON.stringify(headers));
    const refusal = rejectClientImpersonationHeaders(headers);
    assert.equal(refusal.status, 501);
    assert.equal((await refusal.json()).error.code, "tls_fingerprint_stealth_disabled");
  }
  for (const headers of [
    {},
    null,
    { "User-Agent": "acme-gateway-client/1.0", "X-Team": "platform" },
    { "X-App": "dashboard" },
    { originator: "internal-tool" },
  ]) {
    assert.deepEqual(findClientImpersonationHeaders(headers), [], JSON.stringify(headers));
    assert.equal(rejectClientImpersonationHeaders(headers), null);
  }
});

test("provider-nodes create and update refuse a CLI identity header, and the VibeProxy preset answers a named 501", async () => {
  const create = await import(`${SRC}app/api/provider-nodes/route.ts`);
  const update = await import(`${SRC}app/api/provider-nodes/[id]/route.ts`);
  const post = (body) =>
    create.POST(
      new Request("http://127.0.0.1/api/provider-nodes", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      })
    );
  const node = { type: "openai-compatible", name: "gw", prefix: "gwtest", apiType: "chat", baseUrl: "https://gw.example.invalid/v1" };

  for (const customHeaders of [
    { "User-Agent": "claude-cli/2.1.258 (external, cli)", "X-App": "cli" },
    { "User-Agent": "codex_cli_rs/0.1.0", originator: "codex_cli_rs" },
  ]) {
    const res = await post({ ...node, customHeaders });
    assert.equal(res.status, 501, JSON.stringify(customHeaders));
    assert.equal((await res.json()).error.code, "tls_fingerprint_stealth_disabled");
    const put = await update.PUT(
      new Request("http://127.0.0.1/api/provider-nodes/openai-compatible-chat-x", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: "gw", prefix: "gwtest", apiType: "chat", baseUrl: node.baseUrl, customHeaders }),
      }),
      { params: Promise.resolve({ id: "openai-compatible-chat-x" }) }
    );
    assert.equal(put.status, 501, JSON.stringify(customHeaders));
    assert.equal((await put.json()).error.code, "tls_fingerprint_stealth_disabled");
  }

  // Control: an ordinary custom header still creates the node.
  const ok = await post({ ...node, customHeaders: { "User-Agent": "acme-gateway-client/1.0" } });
  assert.equal(ok.status, 201);

  for (const preset of ["vibeproxy-openai", "anything"]) {
    const res = await post({ preset, baseUrl: "http://127.0.0.1:8318" });
    assert.equal(res.status, 501, preset);
    assert.equal((await res.json()).error.code, "consumer_subscription_pooling_disabled");
  }
  const schemas = await import(`${SRC}shared/validation/schemas/provider.ts`);
  const parsed = schemas.createProviderNodeSchema.safeParse({ ...node, preset: "vibeproxy-openai" });
  assert.equal(parsed.success, true);
  assert.equal("preset" in parsed.data, false, "the schema no longer carries a preset field");
  const routeSource = readFileSync(`${SRC}app/api/provider-nodes/route.ts`, "utf8");
  assert.doesNotMatch(routeSource, /vibeproxy-openai|VIBEPROXY_OPENAI|sanitizeVibeProxy/);
});

test("a stored CLI identity header on a custom node is not sent", async () => {
  const { DefaultExecutor } = await import(`${OPEN_SSE}executors/default.ts`);
  const executor = new DefaultExecutor("openai-compatible-chat-legacy");
  const headers = executor.buildHeaders(
    {
      apiKey: "sk-test",
      providerSpecificData: {
        baseUrl: "https://gw.example.invalid/v1",
        customHeaders: {
          "User-Agent": "claude-cli/2.1.258 (external, cli)",
          "X-App": "cli",
          "X-Team": "platform",
        },
      },
    },
    false
  );
  const names = Object.keys(headers).map((name) => name.toLowerCase());
  assert.ok(names.includes("x-team"), "an ordinary custom header is still applied");
  assert.ok(!names.includes("x-app"));
  assert.doesNotMatch(String(headers["User-Agent"] ?? headers["user-agent"] ?? ""), /claude-cli/);
});
