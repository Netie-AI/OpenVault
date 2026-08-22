import assert from "node:assert/strict";
import { test } from "node:test";
import { FORBIDDEN_SUBSCRIBE_TERMS } from "./copy.ts";
import { renderFree, renderPage, renderSubscribe } from "./render.ts";

test("subscribe page HTML stays vendor-clean", () => {
  const html = renderPage("subscribe", { issuedToken: "ov_loopback_demo" });
  assert.match(html, /data-testid="subscribe-screen"/);
  assert.match(html, /Cortex API key/);
  assert.match(html, /Powered by top-tier AI/);
  for (const term of FORBIDDEN_SUBSCRIBE_TERMS) {
    assert.equal(html.includes(term), false, `subscribe HTML leaked ${term}`);
  }
});

test("free screen is register then install", () => {
  const html = renderFree();
  assert.match(html, /data-testid="free-step-1"/);
  assert.match(html, /data-testid="free-step-2"/);
  assert.match(html, />Register</);
  assert.match(html, />Install</);
});
