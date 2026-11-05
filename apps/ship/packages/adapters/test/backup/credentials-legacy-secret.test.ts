// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// Backup destination credentials are sealed with enc1: under the install secret. Values an
// earlier build sealed under the public placeholder must keep opening once the API hands
// this package the placeholder as a fallback, and nothing opens without it.
import { createCipheriv, createHash, randomBytes } from "node:crypto";
import { afterEach, describe, expect, it } from "vitest";
import { decryptCredential, setBackupCredentialSecret } from "../../src/backup/common/credentials";

const PLACEHOLDER = "change-me-in-production";

function seal(secret: string, plain: string): string {
  const iv = randomBytes(16);
  const cipher = createCipheriv("aes-256-gcm", createHash("sha256").update(secret).digest(), iv);
  const body = Buffer.concat([cipher.update(plain, "utf8"), cipher.final()]);
  return "enc1:" + Buffer.concat([iv, cipher.getAuthTag(), body]).toString("base64");
}

afterEach(() => setBackupCredentialSecret(undefined));

describe("decryptCredential with a legacy fallback", () => {
  it("opens a value sealed under the install secret", () => {
    setBackupCredentialSecret("install-secret-".padEnd(64, "x"), [PLACEHOLDER]);
    expect(decryptCredential(seal("install-secret-".padEnd(64, "x"), "REVIEWSECRET_S3SK_102"))).toBe("REVIEWSECRET_S3SK_102");
  });

  it("opens a value an earlier build sealed under the placeholder", () => {
    setBackupCredentialSecret("install-secret-".padEnd(64, "x"), [PLACEHOLDER]);
    expect(decryptCredential(seal(PLACEHOLDER, "REVIEWSECRET_S3SK_102"))).toBe("REVIEWSECRET_S3SK_102");
  });

  it("does not open a placeholder-sealed value unless the fallback is given", () => {
    setBackupCredentialSecret("install-secret-".padEnd(64, "x"));
    expect(() => decryptCredential(seal(PLACEHOLDER, "x"))).toThrow();
  });

  it("still rejects a value sealed under an unrelated key", () => {
    setBackupCredentialSecret("install-secret-".padEnd(64, "x"), [PLACEHOLDER]);
    expect(() => decryptCredential(seal("some-other-installs-secret", "x"))).toThrow();
  });
});
