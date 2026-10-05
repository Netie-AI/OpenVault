/**
 * Sealed gate: status chrome plus the unseal control in VaultSealBar.
 * No network.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  SEALED_GATE_BODY,
  SEALED_GATE_TITLE,
  sealChrome,
  sealedGateOpen,
} from "./sealedGate.ts";

test("sealed status opens the gate; open status does not", () => {
  assert.equal(sealChrome(true), "sealed");
  assert.equal(sealChrome(false), "open");
  assert.equal(sealChrome(undefined), "unknown");
  assert.equal(sealedGateOpen(true, false), true);
  assert.equal(sealedGateOpen(true, true), false);
  assert.equal(sealedGateOpen(false, false), false);
  assert.equal(sealedGateOpen(undefined, false, true), true);
  assert.equal(sealedGateOpen(undefined, true, true), false);
  assert.match(SEALED_GATE_TITLE, /sealed/i);
  assert.match(SEALED_GATE_BODY, /passphrase once/i);
});

test("VaultSealBar renders the sealed gate and unseals in-app", () => {
  const src = readFileSync(
    new URL("../../components/vault/VaultSealBar.tsx", import.meta.url),
    "utf8",
  );
  assert.equal(src.includes('data-testid="sealed-gate"'), true);
  assert.equal(src.includes('data-testid="vault-seal-state"'), true);
  assert.equal(src.includes("unsealVault"), true);
  assert.equal(src.includes("fetchVaultStatus"), true);
  assert.equal(src.includes("writeAdminSession"), true);
  assert.equal(src.includes("SEALED_GATE_TITLE"), true);
  assert.equal(src.includes("SEALED_GATE_BODY"), true);
  assert.equal(src.includes("sealedGateOpen"), true);
  assert.equal(src.includes("rememberSessionPassphrase"), true);
  assert.equal(src.includes("clearSessionPassphrase"), true);
  assert.equal(src.includes("localStorage"), false);
  assert.equal(src.toLowerCase().includes("console.log"), false);
  assert.equal(src.toLowerCase().includes("admin_token"), false);
});
