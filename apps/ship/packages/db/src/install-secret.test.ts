// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// FreeBuild used to seal stored secrets (S3/SFTP backup keys, clone tokens, SSH credentials)
// under the public placeholder `change-me-in-production` whenever BETTER_AUTH_SECRET was
// unset. A fresh install now gets a random per-install secret in a 0600 file, an operator's
// own value still wins, and values sealed under the old placeholder keep opening.
import { mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync, existsSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createEncryption, DEFAULT_ENCRYPTION_SECRET } from "./encryption";
import {
  INSTALL_SECRET_FILENAME,
  installSecretPath,
  isPlaceholderSecret,
  resolveInstallSecret,
} from "./install-secret";

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "freebuild-install-secret-"));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

const posix = process.platform !== "win32";

describe("resolveInstallSecret", () => {
  it("generates a random secret on a fresh install and persists it with mode 0600", () => {
    const file = join(dir, "state", INSTALL_SECRET_FILENAME);
    const first = resolveInstallSecret({ envSecret: undefined, file, testMode: false });

    expect(first.source).toBe("generated");
    expect(first.secret).toMatch(/^[0-9a-f]{64}$/);
    expect(first.secret).not.toBe(DEFAULT_ENCRYPTION_SECRET);
    expect(readFileSync(file, "utf8").trim()).toBe(first.secret);
    if (posix) expect(statSync(file).mode & 0o777).toBe(0o600);

    const second = resolveInstallSecret({ envSecret: undefined, file, testMode: false });
    expect(second).toMatchObject({ source: "file", secret: first.secret });
  });

  it("gives two installs two different secrets", () => {
    const a = resolveInstallSecret({ file: join(dir, "a"), testMode: false });
    const b = resolveInstallSecret({ file: join(dir, "b"), testMode: false });
    expect(a.secret).not.toBe(b.secret);
  });

  it("lets an operator-set BETTER_AUTH_SECRET win and writes no file", () => {
    const file = join(dir, INSTALL_SECRET_FILENAME);
    const out = resolveInstallSecret({ envSecret: "operator-chosen-secret-value", file, testMode: false });
    expect(out).toEqual({ secret: "operator-chosen-secret-value", source: "env" });
    expect(existsSync(file)).toBe(false);
  });

  it("treats an empty value and the .env.example placeholder as unset", () => {
    for (const envSecret of ["", "  ", DEFAULT_ENCRYPTION_SECRET]) {
      const file = join(dir, `secret-${envSecret.length}`);
      const out = resolveInstallSecret({ envSecret, file, testMode: false });
      expect(out.source).toBe("generated");
      expect(out.secret).not.toBe(DEFAULT_ENCRYPTION_SECRET);
    }
    expect(isPlaceholderSecret(DEFAULT_ENCRYPTION_SECRET)).toBe(true);
    expect(isPlaceholderSecret(undefined)).toBe(true);
    expect(isPlaceholderSecret("a-real-secret")).toBe(false);
  });

  it("tightens a secret file that is readable by others", () => {
    if (!posix) return;
    const file = join(dir, INSTALL_SECRET_FILENAME);
    writeFileSync(file, `${"a".repeat(64)}\n`, { mode: 0o644 });
    chmodSync(file, 0o644);
    const out = resolveInstallSecret({ file, testMode: false });
    expect(out).toMatchObject({ source: "file", secret: "a".repeat(64) });
    expect(statSync(file).mode & 0o777).toBe(0o600);
  });

  it("replaces an empty file but refuses a short one rather than weaken the key", () => {
    const file = join(dir, INSTALL_SECRET_FILENAME);
    writeFileSync(file, "", { mode: 0o600 });
    expect(resolveInstallSecret({ file, testMode: false }).source).toBe("generated");

    writeFileSync(file, "too-short\n");
    expect(() => resolveInstallSecret({ file, testMode: false })).toThrow(/fewer than 32 characters/);
  });

  it("under a test runner returns the legacy placeholder and touches no file", () => {
    const file = join(dir, INSTALL_SECRET_FILENAME);
    expect(resolveInstallSecret({ file, testMode: true })).toEqual({ secret: DEFAULT_ENCRYPTION_SECRET, source: "test-default" });
    expect(existsSync(file)).toBe(false);
  });
});

describe("installSecretPath", () => {
  it("prefers an explicit file, then OPENSHIP_DATA_DIR, then the PGlite parent, then ~/.openship", () => {
    const home = join(dir, "home");
    mkdirSync(home);
    expect(installSecretPath({ HOME: home, OPENSHIP_AUTH_SECRET_FILE: join(dir, "x", "key") })).toBe(join(dir, "x", "key"));
    expect(installSecretPath({ HOME: home, OPENSHIP_DATA_DIR: join(dir, "state") })).toBe(join(dir, "state", INSTALL_SECRET_FILENAME));
    expect(installSecretPath({ HOME: home, PGLITE_DATA_DIR: join(dir, "vol", "pglite") })).toBe(join(dir, "vol", INSTALL_SECRET_FILENAME));
    expect(installSecretPath({ HOME: home, PGLITE_DATA_DIR: "~/.openship/data-x" })).toBe(join(home, ".openship", INSTALL_SECRET_FILENAME));
    expect(installSecretPath({ HOME: home, PGLITE_DATA_DIR: "memory://" })).toBe(join(home, ".openship", INSTALL_SECRET_FILENAME));
    expect(installSecretPath({ HOME: home })).toBe(join(home, ".openship", INSTALL_SECRET_FILENAME));
  });
});

describe("an install that stored data under the old placeholder", () => {
  const options = { fallbackSecrets: [DEFAULT_ENCRYPTION_SECRET] };

  it("keeps decrypting it and re-seals it under the install secret on write", () => {
    const legacy = createEncryption(DEFAULT_ENCRYPTION_SECRET).encrypt("LENSSECRET-s3-0123456789abcdef");
    const install = resolveInstallSecret({ file: join(dir, INSTALL_SECRET_FILENAME), testMode: false });
    const encryption = createEncryption(install.secret, options);

    const plain = encryption.decrypt(legacy);
    expect(plain).toBe("LENSSECRET-s3-0123456789abcdef");

    const resealed = encryption.encrypt(plain);
    expect(createEncryption(install.secret).decrypt(resealed)).toBe(plain);
    // The public placeholder no longer opens anything written from now on.
    expect(() => createEncryption(DEFAULT_ENCRYPTION_SECRET).decrypt(resealed)).toThrow();
  });

  it("still refuses a value no known key opens, and a closed context", () => {
    const foreign = createEncryption("some-other-installs-secret-xxxxxxxxxxxxxxxx").encrypt("x");
    const encryption = createEncryption("a".repeat(64), options);
    expect(() => encryption.decrypt(foreign)).toThrow();
    expect(encryption.decryptEnvMap({ A: foreign, B: encryption.encrypt("ok") })).toEqual({ B: "ok" });
    encryption.close();
    expect(() => encryption.decrypt(foreign)).toThrow(/closed/);
  });

  it("does not open placeholder-sealed values unless the caller lists the fallback", () => {
    const legacy = createEncryption(DEFAULT_ENCRYPTION_SECRET).encrypt("x");
    expect(() => createEncryption("a".repeat(64)).decrypt(legacy)).toThrow();
  });
});
