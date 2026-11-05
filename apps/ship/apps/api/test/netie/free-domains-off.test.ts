// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// With HOST_DOMAIN unset there is no base domain for free project domains (the
// fallback used to be the upstream project's cloud domain). The deploy preflight
// says so instead of naming a domain the operator does not own.
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@repo/db", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@repo/db")>();
  return { ...actual, repos: {} };
});
vi.mock("../../src/lib/controller-helpers", () => ({ platform: () => ({ target: "desktop" }) }));
vi.mock("@repo/platform/engine/lib/cloud/client", () => ({ cloudClient: vi.fn() }));
vi.mock("@repo/platform/engine/lib/cloud/session", () => ({
  isCloudConnectedForOrg: vi.fn().mockResolvedValue(false),
}));
vi.mock("@repo/platform/engine/lib/cloud-preflight", () => ({ runCloudPreflight: vi.fn() }));
vi.mock("@repo/platform/engine/lib/dns-resolver", () => ({
  resolveRecords: vi.fn().mockResolvedValue([]),
  lookupAddresses: vi.fn().mockResolvedValue([]),
}));

async function load(hostDomain: string | undefined) {
  vi.resetModules();
  if (hostDomain) process.env.HOST_DOMAIN = hostDomain;
  else delete process.env.HOST_DOMAIN;
  return import("@repo/platform/engine/modules/deployments/preflight");
}

afterEach(() => {
  delete process.env.HOST_DOMAIN;
});

describe("freeDomainsOffMessage", () => {
  it("explains that HOST_DOMAIN is needed, and names no upstream domain", async () => {
    const { freeDomainsOffMessage } = await load(undefined);
    const message = freeDomainsOffMessage();
    expect(message).toMatch(/HOST_DOMAIN/);
    expect(message).toMatch(/custom domain/);
    expect(message).not.toMatch(/opsh|oblien|openship/i);
  });

  it("is silent when HOST_DOMAIN is set", async () => {
    const { freeDomainsOffMessage } = await load("apps.example.test");
    expect(freeDomainsOffMessage()).toBeNull();
  });
});
