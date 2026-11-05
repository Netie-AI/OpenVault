// Copyright (c) 2026 Netie AI. MIT.
//
// Upstream names in FreeRoute. DR-0018 hard line 4: the upstream's names and
// logos do not appear in shipped UI, headers a third party sees, public assets
// or the shipped .env.example. They stay only in notices and About, and in the
// internal identifiers listed in KEPT below. A header that was renamed keeps
// its old name as a twin, and every reader accepts both.

import { test, after } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, readdirSync, existsSync, statSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

process.env.DATA_DIR = mkdtempSync(join(tmpdir(), "freeroute-names-"));
after(() => rmSync(process.env.DATA_DIR, { recursive: true, force: true }));
process.env.DISABLE_SQLITE_AUTO_BACKUP = "true";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");
const exists = (rel) => existsSync(join(ROOT, rel));

const headers = await import("../src/shared/constants/headers.ts");
const {
  OMNIROUTE_RESPONSE_HEADERS,
  FREEROUTE_REQUEST_HEADERS,
  FREEROUTE_ROUTING_RESPONSE_HEADERS,
  legacyHeaderName,
  withLegacyHeaderTwins,
  readFreeRouteHeader,
  readSelectedConnectionId,
} = headers;

// Identifiers that keep the upstream spelling on purpose. Each is a wire id,
// an environment variable name, a file or a path that something else keys on.
const KEPT = [
  [/\b[A-Z0-9_]*OMNI[A-Z0-9_]*\b/g, "environment variable names (OMNIROUTE_*, OMNI_*)"],
  [/x-omni(?:route)?-[a-z0-9-]+/gi, "legacy header twins (X-OmniRoute-*) and the CDP proxy token header"],
  [/\bsk_omniroute\b/g, "default local key sent by tool configs"],
  [/\bomniroute_[a-z_]+/g, "MCP tool names and the Qdrant collection"],
  [/\bomniroute-[a-z0-9.:_-]+/g, "plugin, backup, relay, redis and service-worker identifiers"],
  [/~?\/?\.omniroute\b/g, "default data directory"],
  [/\/omniroute\b/g, "paths and URL subpath examples"],
  [/\bomniroute:/g, "default Redis key prefix"],
  [/\bomniroute\//g, "tool-config provider id (omniroute/<model>)"],
  [/\[omniroute(?: [a-z]+)?\]/g, "log prefixes"],
  [/<code>omniglyph<\/code>/g, "compression engine id"],
  [/\bomni-providers\b/g, "agent skill id"],
];

function strip(text) {
  let out = text;
  for (const [pattern] of KEPT) out = out.replace(pattern, " ");
  return out;
}

function offenders(lines) {
  return lines.filter((line) => /omni/i.test(strip(line)));
}

function walk(dir, visit, skip = new Set(["node_modules", ".build", ".next", ".git"])) {
  for (const name of readdirSync(dir)) {
    if (skip.has(name)) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, visit, skip);
    else visit(full);
  }
}

// ---- Telemetry and control headers (finding 19) ----------------------------

const twin = (name) => legacyHeaderName(name);
const lowerKeys = (obj) => Object.fromEntries(Object.entries(obj).map(([k, v]) => [k.toLowerCase(), v]));
const headersOf = (response) => Object.fromEntries(response.headers.entries());

test("legacyHeaderName maps X-FreeRoute-* to X-OmniRoute-* and nothing else", () => {
  assert.equal(twin("X-FreeRoute-Model"), "X-OmniRoute-Model");
  assert.equal(twin("x-freeroute-model"), "x-omniroute-model");
  assert.equal(twin("X-Request-Id"), null);
  assert.equal(twin("X-OmniRoute-Model"), null);
  const out = withLegacyHeaderTwins({ "X-FreeRoute-A": "1", "Content-Type": "text/plain" });
  assert.deepEqual(out, {
    "X-FreeRoute-A": "1",
    "Content-Type": "text/plain",
    "X-OmniRoute-A": "1",
  });
});

test("response telemetry headers are sent under both names with the same value", async () => {
  const { buildOmniRouteResponseMetaHeaders } = await import("../src/domain/omnirouteResponseMeta.ts");
  const built = buildOmniRouteResponseMetaHeaders({
    provider: "openai",
    model: "gpt-4o",
    cacheHit: true,
    latencyMs: 120,
    ttftMs: 20,
    usage: { prompt_tokens: 3, completion_tokens: 5 },
    costUsd: 0.0001,
    costSavedUsd: 0.5,
    fallbackAttempts: 2,
    requestId: "req-1",
    strategy: "priority",
  });
  const keys = lowerKeys(built);
  for (const name of Object.values(OMNIROUTE_RESPONSE_HEADERS)) {
    const legacy = twin(name).toLowerCase();
    const emitted = name.toLowerCase() in keys;
    if (!emitted) continue;
    assert.equal(keys[legacy], keys[name.toLowerCase()], `${name} has a twin with the same value`);
  }
  for (const required of ["x-freeroute-response-cost", "x-omniroute-response-cost", "x-freeroute-tokens-in", "x-omniroute-tokens-in", "x-freeroute-version", "x-omniroute-version"]) {
    assert.ok(required in keys, `${required} is sent`);
  }
});

test("chat response header builders send every FreeRoute header with its twin", async () => {
  const { buildNonStreamingResponseHeaders } = await import("../open-sse/handlers/chatCore/nonStreamingResponseHeaders.ts");
  const { assembleStreamingResponseHeaders } = await import("../open-sse/handlers/chatCore/streamingResponseHeaders.ts");
  const plain = buildNonStreamingResponseHeaders({
    provider: "openai",
    model: "gpt-4o",
    startTime: Date.now() - 50,
    responseUsage: { prompt_tokens: 1, completion_tokens: 2 },
    estimatedCost: 0.001,
    requestId: "req-2",
    compressionResponseMeta: "lite; source=request-header",
  });
  const streamed = assembleStreamingResponseHeaders({
    providerHeaders: new Headers(),
    provider: "openai",
    model: "gpt-4o",
    pendingRequestId: "req-3",
    compressionResponseMeta: "lite; source=request-header",
  });
  for (const built of [plain, streamed]) {
    const sent = lowerKeys(built);
    const freerouteNames = Object.keys(sent).filter((key) => key.startsWith("x-freeroute-"));
    assert.ok(freerouteNames.includes("x-freeroute-cache"));
    assert.ok(freerouteNames.includes("x-freeroute-compression"));
    for (const name of freerouteNames) {
      assert.equal(sent[twin(name)], sent[name], `${name} has a twin`);
    }
  }
});

test("the cache bypass header works under either name", async () => {
  const { isCacheableForRead } = await import("../src/lib/semanticCache.ts");
  const body = { temperature: 0 };
  assert.equal(isCacheableForRead(body, new Headers()), true);
  assert.equal(isCacheableForRead(body, new Headers({ "X-FreeRoute-No-Cache": "true" })), false);
  assert.equal(isCacheableForRead(body, new Headers({ "X-OmniRoute-No-Cache": "true" })), false);
  assert.equal(isCacheableForRead(body, { "x-freeroute-no-cache": "true" }), false);
  assert.equal(isCacheableForRead(body, { "x-omniroute-no-cache": "true" }), false);
});

test("the semantic cache manager bypasses on either no-cache name", async () => {
  const { SemanticCacheManager } = await import("../open-sse/services/cache/semanticCacheManager.ts");
  const manager = new SemanticCacheManager();
  const body = { temperature: 0 };
  assert.equal(manager.isBypassed(new Headers({ "X-FreeRoute-No-Cache": "true" }), body), true);
  assert.equal(manager.isBypassed(new Headers({ "X-OmniRoute-No-Cache": "true" }), body), true);
  assert.equal(manager.isBypassed(new Headers(), body), false);
});

test("the provider breaker 503 carries both names and combos read either", async () => {
  const { providerCircuitOpenResponse } = await import("../open-sse/utils/error.ts");
  const { isProviderCircuitOpenResult } = await import("../open-sse/services/combo/comboPredicates.ts");
  const response = providerCircuitOpenResponse("openai", 30);
  const sent = lowerKeys(headersOf(response));
  assert.equal(sent["x-freeroute-provider-breaker"], "open");
  assert.equal(sent["x-omniroute-provider-breaker"], "open");
  for (const name of ["X-FreeRoute-Provider-Breaker", "X-OmniRoute-Provider-Breaker"]) {
    assert.equal(
      isProviderCircuitOpenResult({ status: 503, headers: new Headers({ [name]: "open" }) }, ""),
      true,
      name
    );
  }
  assert.equal(isProviderCircuitOpenResult({ status: 503, headers: new Headers() }, ""), false);
});

test("the plugin manifest header is sent under both names", async () => {
  const { getProviderPluginManifestHeader } = await import("../open-sse/config/providerPluginManifestUrl.ts");
  const sent = lowerKeys(getProviderPluginManifestHeader("http://127.0.0.1:3020"));
  assert.ok(sent["x-freeroute-provider-manifest-url"]);
  assert.equal(sent["x-omniroute-provider-manifest-url"], sent["x-freeroute-provider-manifest-url"]);
});

test("peer trace is sent under both names and a loop is caught through either", async () => {
  const { applyPeerTraceHeader, inspectPeerRequest } = await import("../src/shared/resilience/peerRouting.ts");
  const env = { OMNIROUTE_INSTANCE_ID: "node-a", OMNIROUTE_PEER_URLS: "http://peer.local:3020" };
  const out = {};
  assert.equal(applyPeerTraceHeader(out, {}, "http://peer.local:3020/v1/chat/completions", env), true);
  const sent = lowerKeys(out);
  assert.equal(sent["x-freeroute-peer-trace"], "node-a");
  assert.equal(sent["x-omniroute-peer-trace"], "node-a");
  assert.equal(inspectPeerRequest({ "x-freeroute-peer-trace": "node-a" }, env)?.code, "peer_loop_detected");
  assert.equal(inspectPeerRequest({ "x-omniroute-peer-trace": "node-a" }, env)?.code, "peer_loop_detected");
  assert.equal(inspectPeerRequest({}, env), null);
  const next = {};
  applyPeerTraceHeader(next, { "x-omniroute-peer-trace": "node-z" }, "http://peer.local:3020", env);
  assert.equal(lowerKeys(next)["x-freeroute-peer-trace"], "node-z,node-a");
});

test("the compression echo carries both names", async () => {
  const { withCompressionHeaderEcho } = await import("../src/shared/utils/compressionHeaderEcho.ts");
  const sent = lowerKeys(headersOf(withCompressionHeaderEcho(new Response("ok"), "off")));
  assert.equal(sent["x-freeroute-compression"], "off; source=request-header");
  assert.equal(sent["x-omniroute-compression"], "off; source=request-header");
});

test("selected connection and session id headers carry both names", async () => {
  const { withSelectedConnectionHeader, withSessionHeader } = await import("../src/sse/handlers/chatHelpers.ts");
  const picked = lowerKeys(headersOf(withSelectedConnectionHeader(new Response("ok"), "conn-1")));
  assert.equal(picked["x-freeroute-selected-connection-id"], "conn-1");
  assert.equal(picked["x-omniroute-selected-connection-id"], "conn-1");
  const session = lowerKeys(headersOf(withSessionHeader(new Response("ok"), "sess-1")));
  assert.equal(session["x-freeroute-session-id"], "sess-1");
  assert.equal(session["x-omniroute-session-id"], "sess-1");
});

test("readers accept either name, in a Headers object or a plain object", () => {
  for (const source of [
    new Headers({ "X-FreeRoute-Connection": "c1" }),
    new Headers({ "X-OmniRoute-Connection": "c1" }),
    { "x-freeroute-connection": "c1" },
    { "X-OmniRoute-Connection": "c1" },
  ]) {
    assert.equal(readFreeRouteHeader(source, FREEROUTE_REQUEST_HEADERS.connection), "c1");
  }
  assert.equal(readFreeRouteHeader(new Headers(), FREEROUTE_REQUEST_HEADERS.connection), null);
  assert.equal(
    readFreeRouteHeader(
      new Headers({ "X-FreeRoute-Connection": "new", "X-OmniRoute-Connection": "old" }),
      FREEROUTE_REQUEST_HEADERS.connection
    ),
    "new"
  );
  assert.equal(readSelectedConnectionId(new Headers({ "X-OmniRoute-Selected-Connection-Id": "c9" })), "c9");
  assert.equal(readSelectedConnectionId(new Headers({ "X-FreeRoute-Selected-Connection-Id": "c8" })), "c8");
  assert.equal(readSelectedConnectionId(new Headers()), undefined);
});

test("the request readers use the either-name helper", () => {
  assert.match(read("src/sse/handlers/chat.ts"), /readFreeRouteHeader\(request\.headers, FREEROUTE_REQUEST_HEADERS\.connection\)/);
  assert.doesNotMatch(read("src/sse/handlers/chat.ts"), /headers\.get\("x-omniroute-connection"\)/);
  const chatCore = read("open-sse/handlers/chatCore.ts");
  assert.equal((chatCore.match(/FREEROUTE_REQUEST_HEADERS\.sessionId/g) || []).length, 2);
  for (const file of [
    "open-sse/services/combo/executeTargetAttempt.ts",
    "open-sse/services/combo/roundRobinCombo.ts",
  ]) {
    assert.doesNotMatch(read(file), /get\("[Xx]-[Oo]mni[Rr]oute-Selected-Connection-Id"\)/, file);
    assert.match(read(file), /readSelectedConnectionId\(/, file);
  }
  assert.ok(FREEROUTE_ROUTING_RESPONSE_HEADERS.selectedConnectionId);
});

// ---- Banner and promo card (findings 20 and 28) -----------------------------

test("the VS Code extension banner and the Kimi promo card are gone", () => {
  for (const file of [
    "src/app/(dashboard)/dashboard/VscodeCopilotBanner.tsx",
    "src/app/(dashboard)/dashboard/combos/KimiComboPresetCard.tsx",
    "src/app/(dashboard)/dashboard/combos/kimiComboPreset.ts",
  ]) {
    assert.equal(exists(file), false, file);
  }
  assert.doesNotMatch(read("src/app/(dashboard)/home/page.tsx"), /VscodeCopilotBanner|Banner.*omni/i);
  assert.doesNotMatch(read("src/app/(dashboard)/dashboard/combos/page.tsx"), /KimiComboPresetCard|kimiComboPreset/);
  const messages = JSON.parse(read("src/i18n/messages/en.json"));
  assert.equal(messages.vscodeCopilotBanner, undefined);
  for (const key of ["kimiPresetTitle", "kimiPresetDescription", "kimiPresetCta"]) {
    for (const section of Object.values(messages)) {
      if (section && typeof section === "object") assert.equal(section[key], undefined, key);
    }
  }
});

// ---- Radar (finding 21) ----------------------------------------------------

test("Radar has no upstream host, link or feed default", async () => {
  const saved = {
    RADAR_FEED_URL: process.env.RADAR_FEED_URL,
    RADAR_CONTRIBUTOR_CLAIM_URL: process.env.RADAR_CONTRIBUTOR_CLAIM_URL,
    RADAR_SUPPORTER_PLANS_URL: process.env.RADAR_SUPPORTER_PLANS_URL,
  };
  delete process.env.RADAR_FEED_URL;
  delete process.env.RADAR_CONTRIBUTOR_CLAIM_URL;
  delete process.env.RADAR_SUPPORTER_PLANS_URL;
  try {
    const { getContributorClaimUrl, getSupporterPlansUrl } = await import("../src/lib/radar/links.ts");
    const { getRadarFeedBaseUrl, RADAR_UPSTREAM_NOT_INCLUDED } = await import("../src/lib/radar/feedBaseUrl.ts");
    assert.equal(getContributorClaimUrl(), null);
    assert.equal(getSupporterPlansUrl(), null);
    assert.equal(getRadarFeedBaseUrl(), null);
    assert.equal(RADAR_UPSTREAM_NOT_INCLUDED, "upstream_service_not_included");

    const { syncRadar } = await import("../src/lib/radar/sync.ts");
    let fetched = 0;
    const result = await syncRadar({
      fetch: async () => {
        fetched += 1;
        throw new Error("no network call is allowed");
      },
      getFlag: () => true,
      getSettings: () => ({ optIn: true, supporterKey: "omr_" + "a".repeat(40) }),
      getCache: () => null,
      setCache: () => {},
    });
    assert.deepEqual(result, { status: "error", reason: "upstream_service_not_included" });
    assert.equal(fetched, 0);

    const { syncRadarIntel } = await import("../src/lib/radar/intelSync.ts");
    const { syncRadarOffers } = await import("../src/lib/radar/offersSync.ts");
    const { syncRadarReferrals } = await import("../src/lib/radar/referralsSync.ts");
    const deps = {
      fetch: async () => {
        fetched += 1;
        throw new Error("no network call is allowed");
      },
      getFlag: () => true,
      getSettings: () => ({ optIn: true, supporterKey: "omr_" + "a".repeat(40) }),
      getCache: () => null,
      setCache: () => {},
    };
    for (const sync of [syncRadarIntel, syncRadarOffers, syncRadarReferrals]) {
      assert.deepEqual(await sync(deps), { status: "error", reason: "upstream_service_not_included" });
    }
    assert.equal(fetched, 0);

    process.env.RADAR_FEED_URL = "https://feed.example.test/";
    assert.equal(getRadarFeedBaseUrl(), "https://feed.example.test");
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});

test("every /api/radar route answers 501 upstream_service_not_included", async () => {
  const { matchHardDisabledRoute } = await import("../src/lib/netie/hardDisabledRoutes.ts");
  for (const path of ["/api/radar/settings", "/api/radar/sync", "/api/radar/catalog", "/api/radar/offers/sync", "/api/radar/referrals"]) {
    const match = matchHardDisabledRoute(path);
    assert.ok(match, path);
    assert.equal(match.status, 501, path);
    assert.equal(match.body.error.code, "upstream_service_not_included", path);
  }
});

// ---- Assets (findings 22 and 25) -------------------------------------------

// SHA-256 of the upstream brand files that used to ship byte for byte.
const UPSTREAM_BRAND_ASSET_SHA256 = new Map([
  ["1dfca2cd52f3a28c0555b213c11d5d475348f66a1fb5923a1a308d0805edc464", "upstream tray icon.png"],
  ["44c290bbc5a7382f7c674c64385d1500ff7b280ea7de715b9d5cdfb5cc2e8854", "upstream tray icon.ico"],
  ["f97ca160aebf206a557758af5decea2de40b92b35f9385304a8989f909ff5b40", "upstream sponsor logo deyin.svg"],
]);

test("no public or tray asset is an upstream brand file or sponsor asset", () => {
  assert.equal(exists("public/deyin.svg"), false);
  const seen = [];
  for (const dir of ["public", "bin/cli/tray"]) {
    walk(join(ROOT, dir), (file) => {
      const rel = relative(ROOT, file);
      seen.push(rel);
      assert.doesNotMatch(rel, /omni|deyin|sponsor/i, `${rel}: asset name`);
      const sha = createHash("sha256").update(readFileSync(file)).digest("hex");
      assert.equal(UPSTREAM_BRAND_ASSET_SHA256.get(sha), undefined, `${rel} is ${UPSTREAM_BRAND_ASSET_SHA256.get(sha)}`);
    });
  }
  assert.ok(seen.includes("bin/cli/tray/icon.png") && seen.includes("bin/cli/tray/icon.ico"));
  assert.ok(seen.some((file) => file.startsWith("public/")));
});

test("the tray icons are the FreeRoute mark: PNG 32x32 and an ICO with four sizes", () => {
  const png = readFileSync(join(ROOT, "bin/cli/tray/icon.png"));
  assert.equal(png.subarray(1, 4).toString("latin1"), "PNG");
  assert.equal(png.readUInt32BE(16), 32);
  assert.equal(png.readUInt32BE(20), 32);
  const ico = readFileSync(join(ROOT, "bin/cli/tray/icon.ico"));
  assert.equal(ico.readUInt16LE(0), 0);
  assert.equal(ico.readUInt16LE(2), 1);
  assert.equal(ico.readUInt16LE(4), 4);
});

test("the served OpenAPI file documents the FreeRoute header names", () => {
  const yaml = read("public/openapi.yaml");
  assert.match(yaml, /X-FreeRoute-Response-Cost:/);
  assert.doesNotMatch(strip(yaml.replace(/X-OmniRoute-\*?/g, "")), /omni/i);
});

// ---- Strings (findings 26, 29, 31, 38) -------------------------------------

test("no shipped UI string names the upstream", () => {
  const messages = JSON.parse(read("src/i18n/messages/en.json"));
  const entries = [];
  const visit = (value, path) => {
    if (typeof value === "string") entries.push({ path, value });
    else if (value && typeof value === "object") {
      for (const [key, child] of Object.entries(value)) visit(child, `${path}.${key}`);
    }
  };
  visit(messages, "en");
  const bad = entries.filter((entry) => /omni/i.test(strip(entry.value)));
  assert.deepEqual(bad.map((entry) => `${entry.path}: ${entry.value.slice(0, 100)}`), []);
});

test("no UI metadata string names the upstream", () => {
  for (const file of [
    "src/app/(dashboard)/dashboard/conductor/page.tsx",
    "src/shared/constants/sidebarVisibility/sections.ts",
    "open-sse/services/compression/engineCatalog.ts",
  ]) {
    const labels = read(file)
      .split("\n")
      .filter((line) => /^\s*(label|labelFallback|titleFallback|description|title)\s*:/.test(line));
    assert.deepEqual(offenders(labels), [], file);
  }
});

test(".env.example prose names FreeRoute and links nothing upstream", () => {
  const text = read(".env.example");
  assert.doesNotMatch(text, /diegosouzapw|omniroute\.online|github\.com\/[^\s]*OmniRoute/i);
  assert.deepEqual(offenders(text.split("\n")), []);
  const names = text.match(/^#?\s*([A-Z][A-Z0-9_]+)=/gm) || [];
  assert.ok(names.some((line) => /OMNIROUTE_/.test(line)), "OMNIROUTE_* variable names are kept");
});

test("strings sent to third parties say FreeRoute: boundaries and truncation markers", () => {
  const roots = ["open-sse", "src"];
  const hits = [];
  for (const rootDir of roots) {
    walk(join(ROOT, rootDir), (file) => {
      if (!/\.(ts|tsx|mjs)$/.test(file) || /\.test\./.test(file)) return;
      const text = readFileSync(file, "utf8");
      if (/["'`]----OmniRoute|BY OMNIROUTE\]/.test(text)) hits.push(relative(ROOT, file));
    });
  }
  assert.deepEqual(hits, []);
  assert.match(read("open-sse/handlers/audioTranscription.ts"), /----FreeRouteAudioBoundary/);
  assert.match(read("open-sse/handlers/imageGeneration.ts"), /----FreeRouteImageEdit/);
  assert.match(read("src/lib/guardrails/audioBridgeHelpers.ts"), /----FreeRouteAudioBridge/);
  assert.match(read("src/lib/proxyRelay/cloudflareWorkerScript.ts"), /----FreeRouteCFWorker/);
  assert.match(read("open-sse/executors/devin-agentic/serializer.ts"), /BY FREEROUTE\]/);
  assert.match(read("src/lib/skills/followUpTranscript.ts"), /BY FREEROUTE\]/);
});

test("no shipped source links the upstream's hosts or marketplace listing", () => {
  const hits = [];
  walk(join(ROOT, "src"), (file) => {
    if (!/\.(ts|tsx|json|mjs)$/.test(file)) return;
    if (/radar\.omniroute\.online|cloud\.omniroute\.online|itemName=diegosouzapw/.test(readFileSync(file, "utf8"))) {
      hits.push(relative(ROOT, file));
    }
  });
  assert.deepEqual(hits, []);
});

test("the conversation-id and forced-connection request headers are read under either name", () => {
  const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
  const chat = read("../src/sse/handlers/chat.ts");
  assert.match(chat, /readFreeRouteHeader\(request\.headers, "x-omniroute-session-id"\)/);
  assert.doesNotMatch(chat, /request\.headers\.get\("x-omniroute-session-id"\)/);
  const usage = read("../src/lib/usage/internalUsageCommand.ts");
  assert.match(usage, /readFreeRouteHeader\(request\.headers, "x-omniroute-connection"\)/);
  assert.doesNotMatch(usage, /readHeader\(request, "x-omniroute-connection"\)/);
});
