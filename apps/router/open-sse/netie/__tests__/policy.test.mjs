// Copyright (c) 2026 Netie AI. MIT.
//
// Fast unit test for open-sse/netie/policy.ts — runnable without a build.
// Run: node --import tsx open-sse/netie/__tests__/policy.test.mjs
// (wired as `npm run test:policy`, part of `npm test`.)

import { test, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
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
import { matchHardDisabledRoute } from "../../../src/lib/netie/hardDisabledRoutes.ts";

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

test("assertServiceToolAllowed refuses dario, cliproxy and cliproxyapi and nothing else", () => {
  for (const tool of ["dario", "cliproxy", "cliproxyapi", " Dario ", "CLIPROXY"]) {
    assert.equal(isServiceToolDisabled(tool), true, tool);
    assert.throws(
      () => assertServiceToolAllowed(tool),
      (error) => isNetieDisabledError(error) && error.code === DISABLED.consumerSubscription,
      tool
    );
  }
  for (const tool of ["mux", "bifrost", "openwa", "", null, undefined]) {
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

test("the startup bootstrap never supervises or auto-starts dario or cliproxy, whatever the stored row says", async () => {
  const { upsertVersionManagerTool } = await import(`${SRC}lib/db/versionManager.ts`);
  for (const tool of ["dario", "cliproxy"]) {
    await upsertVersionManagerTool({
      tool,
      installedVersion: "1.0.0",
      status: "stopped",
      autoStart: true,
      port: tool === "dario" ? 3456 : 8317,
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
