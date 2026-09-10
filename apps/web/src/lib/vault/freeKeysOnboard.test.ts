import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CF_WORKERS_AI_BASE_TEMPLATE,
  FREE_KEYS_ONBOARD,
  composeCloudflareWorkersAiBase,
  isCloudflareWorkersAiBase,
  isProbeMismatchWarn,
} from "./freeKeysOnboard.ts";

test("Get free keys checklist is Groq-first and skips GitHub Models", () => {
  assert.equal(FREE_KEYS_ONBOARD[0]?.id, "groq");
  assert.equal(FREE_KEYS_ONBOARD[0]?.required_first, true);
  assert.deepEqual(
    FREE_KEYS_ONBOARD.map((row) => row.id),
    ["groq", "google", "openrouter", "cerebras", "mistral", "huggingface", "cloudflare"],
  );
  assert.equal(
    FREE_KEYS_ONBOARD.some((row) => row.id === "github_models" || /github models/i.test(row.label)),
    false,
  );
});

test("Cloudflare Workers AI composes account-id base_url and stays custom", () => {
  const cf = FREE_KEYS_ONBOARD.find((row) => row.id === "cloudflare");
  assert.equal(cf?.add_key_provider, "custom");
  assert.equal(cf?.default_base_url, CF_WORKERS_AI_BASE_TEMPLATE);
  const url = composeCloudflareWorkersAiBase("a".repeat(32));
  assert.equal(
    url,
    "https://api.cloudflare.com/client/v4/accounts/" + "a".repeat(32) + "/ai/v1",
  );
  assert.equal(isCloudflareWorkersAiBase(url), true);
  assert.throws(() => composeCloudflareWorkersAiBase("cf-token-looks-like-a-secret"));
  assert.throws(() => composeCloudflareWorkersAiBase("https://evil.example/x"));
});

test("Hugging Face base_url matches the catalog confirmation", () => {
  const hf = FREE_KEYS_ONBOARD.find((row) => row.id === "huggingface");
  assert.equal(hf?.default_base_url, "https://huggingface.co");
  assert.equal(hf?.add_key_provider, "huggingface");
  assert.equal(hf?.role, "free");
  assert.equal(hf?.custody, "pooled");
});

test("locked checklist register_url, base_url, and provider", () => {
  const expected: Array<[string, string, string, string]> = [
    ["groq", "https://console.groq.com/keys", "https://api.groq.com/openai/v1", "groq"],
    [
      "google",
      "https://aistudio.google.com/apikey",
      "https://generativelanguage.googleapis.com/v1beta/openai",
      "google",
    ],
    ["openrouter", "https://openrouter.ai/keys", "https://openrouter.ai/api/v1", "openrouter"],
    ["cerebras", "https://cloud.cerebras.ai", "https://api.cerebras.ai/v1", "cerebras"],
    ["mistral", "https://console.mistral.ai/api-keys", "https://api.mistral.ai/v1", "mistral"],
    ["huggingface", "https://huggingface.co/settings/tokens", "https://huggingface.co", "huggingface"],
    [
      "cloudflare",
      "https://developers.cloudflare.com/workers-ai/get-started/rest-api/",
      CF_WORKERS_AI_BASE_TEMPLATE,
      "custom",
    ],
  ];
  assert.equal(FREE_KEYS_ONBOARD.length, expected.length);
  for (const [i, [id, registerUrl, baseUrl, provider]] of expected.entries()) {
    const row = FREE_KEYS_ONBOARD[i];
    assert.equal(row?.id, id);
    assert.equal(row?.register_url, registerUrl);
    assert.equal(row?.default_base_url, baseUrl);
    assert.equal(row?.add_key_provider, provider);
    assert.equal(row?.role, "free");
    assert.equal(row?.custody, "pooled");
  }
});

test("CF /models 405 copy is a warn, not key-dead", () => {
  assert.equal(
    isProbeMismatchWarn(
      "HTTP 405 GET /models — probe mismatch, not a dead key; Cloudflare Workers AI /ai/run can still work",
    ),
    true,
  );
  assert.equal(isProbeMismatchWarn("HTTP 401"), false);
});
