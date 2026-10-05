/**
 * In-memory unseal cache. Fixture passphrase only. No network, no storage.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { sealedGateOpen } from "./sealedGate.ts";
import {
  clearSessionPassphrase,
  rememberSessionPassphrase,
  reopenSealedVault,
  sessionPassphrase,
  sessionUnsealed,
} from "./sessionUnseal.ts";

const PHRASE = "session-fixture-passphrase";

test("passphrase stays in memory until lock", () => {
  clearSessionPassphrase();
  try {
    assert.equal(sessionUnsealed(), false);
    rememberSessionPassphrase("  ");
    assert.equal(sessionUnsealed(), false);
    rememberSessionPassphrase(PHRASE);
    assert.equal(sessionUnsealed(), true);
    assert.equal(sessionPassphrase(), PHRASE);
    clearSessionPassphrase();
    assert.equal(sessionUnsealed(), false);
    assert.equal(sessionPassphrase(), "");
  } finally {
    clearSessionPassphrase();
  }
});

test("a sealed server reopens from the session cache", async () => {
  clearSessionPassphrase();
  try {
    let calls = 0;
    const idle = await reopenSealedVault<{ sealed?: boolean }>({ sealed: true }, async () => {
      calls += 1;
      return { sealed: false };
    });
    assert.equal(calls, 0);
    assert.equal(idle.sealed, true);

    rememberSessionPassphrase(PHRASE);
    let seen = "";
    const opened = await reopenSealedVault<{ sealed?: boolean }>({ sealed: true }, async (phrase) => {
      seen = phrase;
      return { sealed: false };
    });
    assert.equal(opened.sealed, false);
    assert.equal(seen, PHRASE);
    assert.equal(sessionUnsealed(), true);

    const still = await reopenSealedVault({ sealed: true }, async () => ({ sealed: true }));
    assert.equal(still.sealed, true);
    assert.equal(sessionUnsealed(), false);
  } finally {
    clearSessionPassphrase();
  }
});

test("a failed re-unseal drops the cache and does not echo it", async () => {
  clearSessionPassphrase();
  try {
    rememberSessionPassphrase(PHRASE);
    await assert.rejects(
      () =>
        reopenSealedVault({ sealed: true }, async (phrase) => {
          throw new Error(`rejected ${phrase}`);
        }),
      (err: unknown) => {
        assert.equal(err instanceof Error, true);
        return true;
      },
    );
    assert.equal(sessionUnsealed(), false);
  } finally {
    clearSessionPassphrase();
  }
});

test("session unlock keeps the gate closed; a 401 still opens it", () => {
  assert.equal(sealedGateOpen(true, false, false, true), false);
  assert.equal(sealedGateOpen(undefined, false, true, true), true);
});

test("session cache module does not touch web storage", () => {
  const src = readFileSync(new URL("./sessionUnseal.ts", import.meta.url), "utf8");
  assert.equal(src.includes("localStorage"), false);
  assert.equal(src.includes("sessionStorage"), false);
  assert.equal(src.toLowerCase().includes("console.log"), false);
});
