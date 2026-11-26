/**
 * The message an ApiError carries for a refused request.
 *
 * No network: each test replaces fetch. The URL uses the reserved `.invalid`
 * name, so a missing stub fails on DNS and never reaches a live :5000.
 */

import assert from "node:assert/strict";
import { test, type TestContext } from "node:test";
import { ApiError, apiPost } from "./client.ts";

const ENGINE_URL = "http://openvault.invalid/api/ship/engine";

/** What `_require_leave_gate` sends for a sealed vault (GateDecision.to_dict()). */
const SEALED_DENIAL = {
  allowed: false,
  action: "deploy",
  reasons: ["vault is sealed; unseal before deploy/leave"],
  keys_ready: false,
  locate: { project_path: "", destination: "cloudflare_pages", sealed: true },
  required_providers: ["openai", "anthropic", "openrouter", "groq", "google"],
};

async function refusal(t: TestContext, body: unknown): Promise<ApiError> {
  const fetchStub = t.mock.method(
    globalThis,
    "fetch",
    async () =>
      new Response(JSON.stringify(body), {
        status: 403,
        statusText: "Forbidden",
        headers: { "Content-Type": "application/json" },
      }),
  );
  try {
    await apiPost(ENGINE_URL, { target: "cloudflare_pages" });
  } catch (err) {
    assert.equal(fetchStub.mock.callCount(), 1);
    assert.ok(err instanceof ApiError, String(err));
    return err;
  }
  assert.fail("a refused request must throw ApiError");
}

test("a leave-gate denial shows its reason, not a bare HTTP 403", async (t) => {
  const err = await refusal(t, { detail: SEALED_DENIAL });
  assert.equal(err.kind, "http");
  assert.equal(err.status, 403);
  assert.equal(err.message, "vault is sealed; unseal before deploy/leave");
  assert.deepEqual(err.detail, SEALED_DENIAL);
});

test("several gate reasons are joined, blank ones dropped", async (t) => {
  const err = await refusal(t, {
    detail: {
      ...SEALED_DENIAL,
      reasons: ["no enabled keys in OpenVault", " ", "need one of providers: openai, groq"],
    },
  });
  assert.equal(err.message, "no enabled keys in OpenVault; need one of providers: openai, groq");
});

test("a string message still wins over reasons, and msg is read", async (t) => {
  const withMessage = await refusal(t, {
    detail: { message: "deploy refused", reasons: ["vault is sealed"] },
  });
  assert.equal(withMessage.message, "deploy refused");

  const withMsg = await refusal(t, { detail: { msg: "field required" } });
  assert.equal(withMsg.message, "field required");
});

test("a detail with no readable text keeps the HTTP status fallback", async (t) => {
  const err = await refusal(t, { detail: { allowed: false, reasons: [] } });
  assert.equal(err.message, "HTTP 403 Forbidden");
});
