// Copyright (c) 2026 Netie AI. MIT.
//
// Fast unit test for open-sse/netie/policy.ts — runnable without a build.
// Run: node --import tsx open-sse/netie/__tests__/policy.test.mjs
// (wired as `npm run test:policy`, part of `npm test`.)

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DISABLED,
  classifyProvider,
  classifyProviderId,
  isProviderHidden,
  NetieDisabledError,
  isNetieDisabledError,
  disabledErrorBody,
} from "../policy.ts";

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
