/**
 * Admin session header for vault unseal and key add.
 * No network. The token in this file is a fixture, not a live credential.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  ADMIN_HEADER,
  ADMIN_SESSION_KEY,
  clearAdminSession,
  mergeAdminHeader,
  pathNeedsAdmin,
  readAdminSession,
  redactShown,
  writeAdminSession,
} from "./adminSession.ts";
import { VAULT_UNSEAL_PATH } from "../vault/sealedGate.ts";

const TOKEN = "unit-admin-session-not-from-a-file";

test("vault unseal and key add take the admin header", () => {
  assert.equal(pathNeedsAdmin(VAULT_UNSEAL_PATH), true);
  assert.equal(pathNeedsAdmin("/api/vault/status"), true);
  assert.equal(pathNeedsAdmin("/api/keys"), true);
  assert.equal(pathNeedsAdmin("/api/freeroute/status"), false);
  assert.equal(pathNeedsAdmin("/keys/jwks"), false);

  const unseal = mergeAdminHeader(VAULT_UNSEAL_PATH, { Accept: "application/json" }, TOKEN);
  assert.equal(unseal[ADMIN_HEADER], TOKEN);

  const keys = mergeAdminHeader("/api/keys", {}, TOKEN);
  assert.equal(keys[ADMIN_HEADER], TOKEN);

  const open = mergeAdminHeader("/api/freeroute/status", {}, TOKEN);
  assert.equal(open[ADMIN_HEADER], undefined);

  const blank = mergeAdminHeader(VAULT_UNSEAL_PATH, {}, "  ");
  assert.equal(blank[ADMIN_HEADER], undefined);

  const kept = mergeAdminHeader("/api/keys", { [ADMIN_HEADER]: "caller" }, TOKEN);
  assert.equal(kept[ADMIN_HEADER], "caller");
});

test("Lock removes openvault.admin; re-entry still authorizes unseal", () => {
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
  try {
    assert.equal(ADMIN_SESSION_KEY, "openvault.admin");
    writeAdminSession(TOKEN);
    assert.equal(sessionStorage.getItem(ADMIN_SESSION_KEY), TOKEN);

    clearAdminSession();
    assert.equal(sessionStorage.getItem("openvault.admin"), null);
    assert.equal(readAdminSession(), "");
    const locked = mergeAdminHeader(VAULT_UNSEAL_PATH, {}, readAdminSession());
    assert.equal(locked[ADMIN_HEADER], undefined);

    writeAdminSession(TOKEN);
    assert.equal(readAdminSession(), TOKEN);
    const again = mergeAdminHeader(VAULT_UNSEAL_PATH, { Accept: "application/json" }, readAdminSession());
    assert.equal(again[ADMIN_HEADER], TOKEN);
  } finally {
    Object.defineProperty(globalThis, "sessionStorage", {
      configurable: true,
      value: orig,
    });
  }
});

test("VaultSealBar Lock clears the admin session with the passphrase", () => {
  const src = readFileSync(
    new URL("../../components/vault/VaultSealBar.tsx", import.meta.url),
    "utf8",
  );
  const lock = src.slice(src.indexOf("async function onLock"), src.indexOf("async function onSetPassphrase"));
  const lockAt = lock.indexOf("lockVault()");
  const passAt = lock.indexOf("clearSessionPassphrase()");
  const adminAt = lock.indexOf("clearAdminSession()");
  assert.equal(lockAt >= 0, true);
  assert.equal(passAt > lockAt, true);
  assert.equal(adminAt > passAt, true);
  assert.equal(lock.includes("clearEnvPaste()"), true);

  const passkey = src.slice(
    src.indexOf("async function onClearPasskey"),
    src.indexOf("async function onRetireBackup"),
  );
  assert.equal(passkey.includes("clearAdminSession"), false);
  assert.equal(src.toLowerCase().includes("console.log"), false);
});

test("shown text drops passphrase and admin token", () => {
  const phrase = "correct-horse-battery";
  const shown = redactShown(`rejected ${phrase} token ${TOKEN}`, [phrase, TOKEN]);
  assert.equal(shown.includes(phrase), false);
  assert.equal(shown.includes(TOKEN), false);
});
