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
  rememberSessionWebAuthn,
  reopenSealedVault,
  sessionPassphrase,
  sessionUnlockKind,
  sessionUnsealed,
} from "./sessionUnseal.ts";

const PHRASE = "session-fixture-passphrase";

type FixtureStatus = { sealed?: boolean; webauthn_registered?: boolean };

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

    const still = await reopenSealedVault<FixtureStatus>({ sealed: true }, async () => ({ sealed: true }));
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
        reopenSealedVault<FixtureStatus>({ sealed: true }, async (phrase) => {
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
  assert.equal(src.includes("/api/"), false);
});

test("passkey unseal fills the same session slot without a passphrase", () => {
  clearSessionPassphrase();
  try {
    rememberSessionWebAuthn();
    assert.equal(sessionUnlockKind(), "webauthn");
    assert.equal(sessionUnsealed(), true);
    assert.equal(sessionPassphrase(), "");
    clearSessionPassphrase();
    assert.equal(sessionUnlockKind(), "none");
    assert.equal(sessionUnsealed(), false);
  } finally {
    clearSessionPassphrase();
  }
});

test("a hello session reopens through the passkey function", async () => {
  clearSessionPassphrase();
  try {
    rememberSessionWebAuthn();
    let passkeyCalls = 0;
    let phraseCalls = 0;
    const opened = await reopenSealedVault<FixtureStatus>(
      { sealed: true, webauthn_registered: true },
      async () => {
        phraseCalls += 1;
        return { sealed: false };
      },
      async () => {
        passkeyCalls += 1;
        return { sealed: false, webauthn_registered: true };
      },
    );
    assert.equal(passkeyCalls, 1);
    assert.equal(phraseCalls, 0);
    assert.equal(opened.sealed, false);
    assert.equal(sessionUnsealed(), true);
    assert.equal(sessionPassphrase(), "");
    assert.equal(sessionUnlockKind(), "webauthn");
  } finally {
    clearSessionPassphrase();
  }
});

test("a registered vault prefers passkey reopen when no passphrase is cached", async () => {
  clearSessionPassphrase();
  try {
    let passkeyCalls = 0;
    const opened = await reopenSealedVault<FixtureStatus>(
      { sealed: true, webauthn_registered: true },
      async () => {
        throw new Error("passphrase should not run");
      },
      async () => {
        passkeyCalls += 1;
        return { sealed: false, webauthn_registered: true };
      },
    );
    assert.equal(passkeyCalls, 1);
    assert.equal(opened.sealed, false);
    assert.equal(sessionUnlockKind(), "webauthn");
    assert.equal(sessionPassphrase(), "");
  } finally {
    clearSessionPassphrase();
  }
});

test("platform refusal keeps the sealed status for the passphrase fallback", async () => {
  clearSessionPassphrase();
  try {
    const opened = await reopenSealedVault<FixtureStatus>(
      { sealed: true, webauthn_registered: true },
      async () => {
        throw new Error("passphrase should not run");
      },
      async () => {
        throw new Error("NotAllowedError");
      },
    );
    assert.equal(opened.sealed, true);
    assert.equal(sessionUnsealed(), false);

    rememberSessionPassphrase(PHRASE);
    let passkeyCalls = 0;
    let seen = "";
    const viaPhrase = await reopenSealedVault<FixtureStatus>(
      { sealed: true, webauthn_registered: true },
      async (phrase) => {
        seen = phrase;
        return { sealed: false, webauthn_registered: true };
      },
      async () => {
        passkeyCalls += 1;
        return { sealed: false, webauthn_registered: true };
      },
    );
    assert.equal(passkeyCalls, 0);
    assert.equal(seen, PHRASE);
    assert.equal(viaPhrase.sealed, false);
    assert.equal(sessionUnlockKind(), "passphrase");
  } finally {
    clearSessionPassphrase();
  }
});

test("a failed hello reopen clears the session and does not call passphrase unseal", async () => {
  clearSessionPassphrase();
  try {
    rememberSessionWebAuthn();
    let phraseCalls = 0;
    await assert.rejects(
      () =>
        reopenSealedVault<FixtureStatus>(
          { sealed: true, webauthn_registered: true },
          async () => {
            phraseCalls += 1;
            return { sealed: false };
          },
          async () => {
            throw new Error("cancelled");
          },
        ),
      (err: unknown) => err instanceof Error && err.message === "cancelled",
    );
    assert.equal(phraseCalls, 0);
    assert.equal(sessionUnsealed(), false);
    assert.equal(sessionPassphrase(), "");
  } finally {
    clearSessionPassphrase();
  }
});

test("hello reopen that stays sealed clears the session", async () => {
  clearSessionPassphrase();
  try {
    rememberSessionWebAuthn();
    const still = await reopenSealedVault<FixtureStatus>(
      { sealed: true, webauthn_registered: true },
      async () => ({ sealed: false }),
      async () => ({ sealed: true, webauthn_registered: true }),
    );
    assert.equal(still.sealed, true);
    assert.equal(sessionUnsealed(), false);
  } finally {
    clearSessionPassphrase();
  }
});

test("a hello session does not reopen when the vault is no longer registered", async () => {
  clearSessionPassphrase();
  try {
    rememberSessionWebAuthn();
    let passkeyCalls = 0;
    const st = await reopenSealedVault<FixtureStatus>(
      { sealed: true, webauthn_registered: false },
      async () => ({ sealed: true }),
      async () => {
        passkeyCalls += 1;
        return { sealed: false };
      },
    );
    assert.equal(passkeyCalls, 0);
    assert.equal(st.sealed, true);
    assert.equal(sessionUnsealed(), false);
  } finally {
    clearSessionPassphrase();
  }
});
