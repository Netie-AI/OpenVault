import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BYOK,
  FORBIDDEN_SUBSCRIBE_TERMS,
  FREE,
  POWERED_BY,
  SAFETY_DISCLOSURE,
  SUBSCRIBE,
} from "./copy.ts";
import { renderSubscribe } from "./render.ts";

function subscribeSurface(): string {
  return [
    SUBSCRIBE.title,
    SUBSCRIBE.lead,
    SUBSCRIBE.button,
    SUBSCRIBE.issuedHeading,
    SUBSCRIBE.issuedHint,
    SUBSCRIBE.disclosure,
    SUBSCRIBE.emptyHint,
    SAFETY_DISCLOSURE,
    POWERED_BY,
    renderSubscribe({ issuedToken: "ov_exampletokenvalue0001" }),
  ].join("\n");
}

test("subscribe copy names Cortex and the safety lock", () => {
  const text = subscribeSurface();
  assert.match(text, /Cortex API key/);
  assert.match(text, /Cortex/);
  assert.match(text, /Safety:/);
  assert.match(text, /Powered by top-tier AI/);
  assert.match(text, /one vault/);
});

test("subscribe copy does not name hop vendors or a fake OpenAI string", () => {
  const text = subscribeSurface();
  for (const term of FORBIDDEN_SUBSCRIBE_TERMS) {
    assert.equal(
      text.includes(term),
      false,
      `subscribe copy must not contain ${term}`,
    );
  }
  assert.equal(text.toLowerCase().includes("openai"), false);
});

test("issued ov_ token is framed as a Cortex API key", () => {
  const html = renderSubscribe({ issuedToken: "ov_issued_once_only" });
  assert.match(html, /Cortex API key/);
  assert.match(html, /ov_issued_once_only/);
  assert.equal(html.includes("OpenAI"), false);
  assert.equal(html.includes("DeepSeek"), false);
});

test("BYOK and free copy stay short and honest", () => {
  assert.match(BYOK.lead, /provider name you brought/);
  assert.equal(FREE.steps.length, 2);
  assert.equal(FREE.steps[0]?.title, "Register");
  assert.equal(FREE.steps[1]?.title, "Install");
});
