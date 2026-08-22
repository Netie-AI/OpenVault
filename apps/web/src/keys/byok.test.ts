import assert from "node:assert/strict";
import { test } from "node:test";
import { guessByokProvider, honestByokLabel } from "./byok.ts";
import { renderByok } from "./render.ts";

test("BYOK shows the provider name the user pasted", () => {
  assert.equal(honestByokLabel("Groq", "gsk_abc"), "Groq");
  assert.equal(honestByokLabel("My lab key", "sk-unknownshape"), "My lab key");
  const html = renderByok({
    pastedSecret: "gsk_live_example",
    pastedProviderName: "Groq",
  });
  assert.match(html, /Stored as <strong>Groq<\/strong>/);
});

test("ov_ is Cortex, never a fake OpenAI vendor string", () => {
  const guess = guessByokProvider("ov_abcdefghijklmnopqrstuv");
  assert.equal(guess.providerId, "cortex");
  assert.equal(guess.displayName, "Cortex API key");
  assert.notEqual(guess.displayName, "OpenAI");
});

test("strong prefixes get honest catalog names", () => {
  assert.equal(guessByokProvider("sk-ant-xxxx").displayName, "Anthropic");
  assert.equal(guessByokProvider("sk-or-v1-xxxx").displayName, "OpenRouter");
  assert.equal(guessByokProvider("sk-proj-abcdefghijklmnopqrstuv").displayName, "OpenAI");
});
