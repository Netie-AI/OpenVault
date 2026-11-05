// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// A tunnel token (Cloudflare Tunnel, ngrok) is a third-party secret. FreeBuild stores none:
// every write path answers 501 keys_managed_by_openvault, an empty value still clears a
// copy an earlier build left, and the boot scrub deletes a stored one.
import { beforeEach, describe, expect, it, vi } from "vitest";

const { settingsRepo } = vi.hoisted(() => ({
  settingsRepo: {
    get: vi.fn(async () => undefined as Record<string, unknown> | undefined),
    upsert: vi.fn(async (patch: Record<string, unknown>) => patch),
  },
}));

vi.mock("@repo/db", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@repo/db")>();
  return { ...actual, repos: new Proxy(actual.repos, { get: (t, k, r) => (k === "instanceSettings" ? settingsRepo : Reflect.get(t, k, r)) }) };
});

import {
  assertNoTunnelToken,
  updateSettings,
} from "@repo/platform/engine/modules/system/settings.operations";
import { setup } from "../../../src/modules/system/setup.controller";
import { scrubStoredTunnelToken } from "../../../src/lib/startup/tunnel-token-scrub";

const ctx = { organizationId: "org_1", userId: "u_1" } as never;

beforeEach(() => {
  vi.clearAllMocks();
  settingsRepo.get.mockResolvedValue(undefined);
});

describe("assertNoTunnelToken", () => {
  it("refuses any non-empty token with 501 keys_managed_by_openvault", () => {
    for (const value of ["REVIEWSECRET_CFTUNNEL_106", " x ", 7, {}, true]) {
      try {
        assertNoTunnelToken(value);
        expect.unreachable("should have thrown");
      } catch (err) {
        expect(err).toMatchObject({ statusCode: 501, code: "keys_managed_by_openvault" });
      }
    }
  });

  it("accepts the values that clear or omit the field", () => {
    for (const value of [undefined, null, "", "   ", false]) {
      expect(() => assertNoTunnelToken(value)).not.toThrow();
    }
  });
});

describe("PATCH /system/settings (updateSettings)", () => {
  it("does not write a tunnel token", async () => {
    await expect(
      updateSettings(ctx, { tunnelProvider: "cloudflare", tunnelToken: "REVIEWSECRET_CFTUNNEL_106" }),
    ).rejects.toMatchObject({ statusCode: 501, code: "keys_managed_by_openvault" });
    expect(settingsRepo.upsert).not.toHaveBeenCalled();
  });

  it("still saves the provider and clears a stale token when the value is empty", async () => {
    await updateSettings(ctx, { tunnelProvider: "cloudflare", tunnelToken: "" });
    expect(settingsRepo.upsert).toHaveBeenCalledTimes(1);
    expect(settingsRepo.upsert).toHaveBeenCalledWith({ tunnelProvider: "cloudflare", tunnelToken: null });
  });
});

describe("POST /system/setup", () => {
  function setupContext(body: Record<string, unknown>) {
    return { req: { json: async () => body }, json: vi.fn((b: unknown, status?: number) => ({ b, status })) } as never;
  }

  it("refuses a tunnel token before writing anything", async () => {
    await expect(setup(setupContext({ tunnelProvider: "cloudflare", tunnelToken: "REVIEWSECRET_CFTUNNEL_106" }))).rejects.toMatchObject({
      statusCode: 501,
      code: "keys_managed_by_openvault",
    });
    expect(settingsRepo.upsert).not.toHaveBeenCalled();
  });
});

describe("boot scrub", () => {
  it("clears a token an earlier build stored", async () => {
    settingsRepo.get.mockResolvedValue({ tunnelToken: "REVIEWSECRET_CFTUNNEL_106" });
    await expect(scrubStoredTunnelToken()).resolves.toBe(true);
    expect(settingsRepo.upsert).toHaveBeenCalledWith({ tunnelToken: null });
  });

  it("does nothing when no token is stored", async () => {
    settingsRepo.get.mockResolvedValue({ tunnelToken: null });
    await expect(scrubStoredTunnelToken()).resolves.toBe(false);
    expect(settingsRepo.upsert).not.toHaveBeenCalled();
  });
});
