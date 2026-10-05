/**
 * Admin session header for vault unseal and key add.
 * No network. The token in this file is a fixture, not a live credential.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ADMIN_HEADER,
  mergeAdminHeader,
  pathNeedsAdmin,
  redactShown,
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

test("shown text drops passphrase and admin token", () => {
  const phrase = "correct-horse-battery";
  const shown = redactShown(`rejected ${phrase} token ${TOKEN}`, [phrase, TOKEN]);
  assert.equal(shown.includes(phrase), false);
  assert.equal(shown.includes(TOKEN), false);
});
