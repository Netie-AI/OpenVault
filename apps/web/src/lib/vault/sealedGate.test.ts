/**
 * Sealed gate: status chrome plus the unseal control in VaultSealBar.
 * No network.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  SEALED_GATE_BODY,
  SEALED_GATE_HELLO_BODY,
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
  assert.match(SEALED_GATE_HELLO_BODY, /Windows Hello/);
  assert.match(SEALED_GATE_HELLO_BODY, /Passphrase still works/);
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
  assert.equal(src.includes("writeAdminSession"), false);
  assert.equal(src.includes("sealed-gate-admin"), false);
  assert.equal(src.includes("Admin token"), false);
  assert.equal(src.includes("SEALED_GATE_TITLE"), true);
  assert.equal(src.includes("SEALED_GATE_BODY"), true);
  assert.equal(src.includes("sealedGateOpen"), true);
  assert.equal(src.includes("rememberSessionPassphrase"), true);
  assert.equal(src.includes("rememberSessionWebAuthn"), true);
  assert.equal(src.includes("unsealVaultWithPasskey"), true);
  assert.equal(src.includes("SEALED_GATE_HELLO_BODY"), true);
  assert.equal(src.includes('data-testid="vault-hello-unlock"'), true);
  assert.equal(src.includes("clearSessionPassphrase"), true);
  assert.equal(src.includes("localStorage"), false);
  assert.equal(src.toLowerCase().includes("console.log"), false);
  assert.equal(src.toLowerCase().includes("admin_token"), false);
});

test("Overview shows the same unseal control", () => {
  const src = readFileSync(new URL("../../app/page.tsx", import.meta.url), "utf8");
  assert.equal(src.includes("<VaultSealBar />"), true);
  assert.equal(src.toLowerCase().includes("admin token"), false);
  assert.equal(src.toLowerCase().includes("admin_token"), false);
});
