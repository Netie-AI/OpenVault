// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
import "../mail/_setup-env";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The ownership TXT record is shown and written as `_freebuild-challenge.<host>`.
 * An install that published `_openship-challenge.<host>` before the rename must keep
 * verifying: both names are accepted, only the FreeBuild one is emitted.
 */

const records = vi.hoisted(() => new Map<string, string[]>());
const resolveRecords = vi.hoisted(() =>
  vi.fn(async (name: string) => records.get(name) ?? []),
);

vi.mock("@repo/db", () => ({
  repos: { domain: {}, project: { findById: vi.fn() } },
  normalizeRoutingFields: vi.fn(),
}));
vi.mock("@repo/platform/engine/lib/dns-resolver", () => ({ resolveRecords }));

const { verifyTxt } = await import("@repo/platform/engine/modules/domains/domain.service");

beforeEach(() => {
  records.clear();
  resolveRecords.mockClear();
});

describe("verifyTxt", () => {
  it("accepts the FreeBuild record name", async () => {
    records.set("_freebuild-challenge.app.example.com", ["tok"]);
    expect(await verifyTxt("app.example.com", "tok")).toBe(true);
  });

  it("still accepts the record name an existing install already published", async () => {
    records.set("_openship-challenge.app.example.com", ["tok"]);
    expect(await verifyTxt("app.example.com", "tok")).toBe(true);
  });

  it("rejects a wrong token under either name", async () => {
    records.set("_freebuild-challenge.app.example.com", ["other"]);
    records.set("_openship-challenge.app.example.com", ["other"]);
    expect(await verifyTxt("app.example.com", "tok")).toBe(false);
  });
});
