import assert from "node:assert/strict";
import { test } from "node:test";
import {
  clearRegisterIntent,
  formatRegisterAgo,
  readRegisterIntent,
  rememberRegisterIntent,
} from "./registerIntent.ts";

test("remember + read register intent round-trips", () => {
  const mem = new Map<string, string>();
  const orig = globalThis.sessionStorage;
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: {
      getItem: (k: string) => mem.get(k) ?? null,
      setItem: (k: string, v: string) => {
        mem.set(k, v);
      },
      removeItem: (k: string) => {
        mem.delete(k);
      },
    },
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: globalThis,
  });
  try {
    rememberRegisterIntent({
      providerId: "groq",
      providerName: "Groq",
      registerUrl: "https://console.groq.com/keys",
    });
    const got = readRegisterIntent();
    assert.equal(got?.providerId, "groq");
    assert.equal(got?.registerUrl.startsWith("https://"), true);
    clearRegisterIntent();
    assert.equal(readRegisterIntent(), null);
    assert.equal(formatRegisterAgo(Date.now() - 1000), "just now");
  } finally {
    Object.defineProperty(globalThis, "sessionStorage", {
      configurable: true,
      value: orig,
    });
  }
});
