// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// A legacy local row for an OpenVault-managed provider (Cloudflare) is never decrypted
// or sent to the provider: verify answers 501 keys_managed_by_openvault, and the
// requireProvider error for an unverifiable provider names FreeBuild.
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  credentialRepo: {
    findById: vi.fn(),
    markVerified: vi.fn(async () => {}),
    markInvalid: vi.fn(async () => {}),
    nameTaken: vi.fn(async () => false),
    create: vi.fn(),
  },
  decrypt: vi.fn((value: string) => value.replace(/^enc1:/, "")),
  verifyCredentialValues: vi.fn(async () => ({ ok: true })),
  hasVerifier: vi.fn(() => true),
}));

vi.mock("@repo/db", () => ({ repos: { credential: h.credentialRepo } }));
vi.mock("@repo/platform/engine/lib/credential-encryption", () => ({
  encryptSecretField: (value: string) => `enc1:${value}`,
  decryptSecretField: h.decrypt,
}));
vi.mock("@repo/platform/engine/modules/credentials/verify", () => ({
  hasVerifier: h.hasVerifier,
  verifyCredentialValues: h.verifyCredentialValues,
}));

import {
  createCredential,
  verifyCredential,
} from "@repo/platform/engine/modules/credentials/credential.service";

const legacyCloudflare = {
  id: "cred_cf",
  organizationId: "org_1",
  provider: "cloudflare",
  name: "Cloudflare production",
  selector: null,
  publicFields: {},
  secretsEnc: `enc1:${JSON.stringify({ apiToken: "legacy-cf-token" })}`,
  status: "active",
  lastVerifiedAt: new Date("2026-01-01"),
  lastError: null,
  createdAt: new Date("2026-01-01"),
  updatedAt: new Date("2026-01-01"),
};

beforeEach(() => {
  vi.clearAllMocks();
  h.hasVerifier.mockReturnValue(true);
});

describe("OpenVault-managed providers in the local credential table", () => {
  it("refuses to verify a legacy Cloudflare row and never reads its token", async () => {
    h.credentialRepo.findById.mockResolvedValue(legacyCloudflare);

    await expect(verifyCredential("org_1", "cred_cf")).rejects.toMatchObject({
      statusCode: 501,
      code: "keys_managed_by_openvault",
    });
    expect(h.decrypt).not.toHaveBeenCalled();
    expect(h.verifyCredentialValues).not.toHaveBeenCalled();
    expect(h.credentialRepo.markVerified).not.toHaveBeenCalled();
    expect(h.credentialRepo.markInvalid).not.toHaveBeenCalled();
  });

  it("names FreeBuild when a provider has no verifier", async () => {
    h.hasVerifier.mockReturnValue(false);
    await expect(
      createCredential("org_1", {
        provider: "docker-registry",
        name: "x",
        selector: "registry.example.com",
        values: { username: "u", secret: "s" },
      }),
    ).rejects.toThrow(/^FreeBuild cannot verify .+ credentials yet\.$/);
    expect(h.credentialRepo.create).not.toHaveBeenCalled();
  });
});
