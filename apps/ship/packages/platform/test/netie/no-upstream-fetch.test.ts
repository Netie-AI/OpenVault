// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// Runtime fetches that used to target the upstream project's repositories must
// make no request in the default configuration (DR-0018 hard line 5).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@repo/db", () => ({ repos: {} }));

function stubFetch() {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    calls.push(String(input));
    return new Response("{}", { status: 404 });
  });
  return calls;
}

describe("GeoIP database", () => {
  beforeEach(() => {
    delete process.env.OPENSHIP_GEOIP_URL;
    delete process.env.OPENSHIP_GEOIP_DB;
  });
  afterEach(() => {
    vi.restoreAllMocks();
    delete process.env.OPENSHIP_GEOIP_URL;
  });

  it("has no default download source", async () => {
    vi.resetModules();
    const { geoipDownloadUrl } = await import("../../src/engine/lib/geo-ip");
    expect(geoipDownloadUrl()).toBeNull();
  });

  it("ignores a URL that names the upstream project and accepts the operator's mirror", async () => {
    vi.resetModules();
    const { geoipDownloadUrl } = await import("../../src/engine/lib/geo-ip");
    process.env.OPENSHIP_GEOIP_URL =
      "https://raw.githubusercontent.com/oblien/openship/main/apps/api/assets/geoip/GeoLite2-Country.mmdb";
    expect(geoipDownloadUrl()).toBeNull();
    process.env.OPENSHIP_GEOIP_URL = "https://mirror.example.test/GeoLite2-Country.mmdb";
    expect(geoipDownloadUrl()).toBe("https://mirror.example.test/GeoLite2-Country.mmdb");
  });

  it("warming the reader makes no request in the default configuration", async () => {
    const calls = stubFetch();
    vi.resetModules();
    const { primeGeo } = await import("../../src/engine/lib/geo-ip");
    await primeGeo(1000);
    expect(calls).toEqual([]);
  });
});

describe("release resolution", () => {
  afterEach(() => vi.restoreAllMocks());

  it("never asks GitHub for the upstream project's latest release", async () => {
    const calls = stubFetch();
    vi.resetModules();
    const { fetchLatestRelease } = await import("../../src/engine/lib/release-resolver");
    expect(await fetchLatestRelease("oblien/openship")).toBeNull();
    expect(await fetchLatestRelease("")).toBeNull();
    expect(calls).toEqual([]);
  });

  it("refuses to download a release artifact from the upstream project", async () => {
    const calls = stubFetch();
    vi.resetModules();
    const { fetchAndExtractRelease } = await import("../../src/engine/lib/release-download");
    const base = { tag: "v9.9.9", cacheDir: "/tmp/freebuild-test-no-such-cache", asset: "x.tar.gz" };
    await expect(fetchAndExtractRelease({ ...base, repo: "oblien/openship" })).rejects.toThrow(/disabled/);
    await expect(fetchAndExtractRelease(base)).rejects.toThrow(/disabled/);
    expect(calls).toEqual([]);
  });
});
