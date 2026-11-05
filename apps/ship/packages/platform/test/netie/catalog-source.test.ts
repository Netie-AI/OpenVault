// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@repo/db", () => ({ repos: {} }));

const UPSTREAM_CATALOG =
  "https://raw.githubusercontent.com/oblien/openship/main/packages/core/src/apps/catalog.json";

function stubFetch() {
  const calls: string[] = [];
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    calls.push(String(input));
    return new Response(JSON.stringify({ version: 1, apps: [] }), { status: 200 });
  });
  return { calls, spy };
}

async function load() {
  vi.resetModules();
  return import("../../src/engine/modules/apps/catalog-source");
}

describe("app catalog source", () => {
  beforeEach(() => {
    delete process.env.FREEBUILD_CATALOG_URL;
  });
  afterEach(() => {
    vi.restoreAllMocks();
    delete process.env.FREEBUILD_CATALOG_URL;
  });

  it("makes no network request in the default configuration", async () => {
    const { calls } = stubFetch();
    const { getRuntimeCatalog } = await load();
    getRuntimeCatalog();
    getRuntimeCatalog();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(calls).toEqual([]);
  });

  it("serves the bundled FreeBuild catalog", async () => {
    stubFetch();
    const { getRuntimeTemplate } = await load();
    expect(getRuntimeTemplate("mail")?.name).toBe("FreeBuild Mail");
  });

  it("ignores FREEBUILD_CATALOG_URL values that are not usable", async () => {
    const { catalogOverlayUrl } = await load();
    expect(catalogOverlayUrl({})).toBeNull();
    expect(catalogOverlayUrl({ FREEBUILD_CATALOG_URL: "not a url" })).toBeNull();
    expect(catalogOverlayUrl({ FREEBUILD_CATALOG_URL: "http://catalog.example.test/c.json" })).toBeNull();
    expect(catalogOverlayUrl({ FREEBUILD_CATALOG_URL: UPSTREAM_CATALOG })).toBeNull();
    expect(
      catalogOverlayUrl({ FREEBUILD_CATALOG_URL: "https://github.com/oblien/openship/raw/main/c.json" }),
    ).toBeNull();
  });

  it("accepts an https or loopback URL the operator controls", async () => {
    const { catalogOverlayUrl } = await load();
    expect(catalogOverlayUrl({ FREEBUILD_CATALOG_URL: "https://catalog.example.test/c.json" })).toBe(
      "https://catalog.example.test/c.json",
    );
    expect(catalogOverlayUrl({ FREEBUILD_CATALOG_URL: "http://127.0.0.1:9000/c.json" })).toBe(
      "http://127.0.0.1:9000/c.json",
    );
  });

  it("fetches only the operator's URL when one is set", async () => {
    process.env.FREEBUILD_CATALOG_URL = "https://catalog.example.test/c.json";
    const { calls } = stubFetch();
    const { getRuntimeCatalog } = await load();
    getRuntimeCatalog();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(calls).toEqual(["https://catalog.example.test/c.json"]);
  });

  it("never fetches the upstream catalog even if the operator names it", async () => {
    process.env.FREEBUILD_CATALOG_URL = UPSTREAM_CATALOG;
    const { calls } = stubFetch();
    const { getRuntimeCatalog } = await load();
    getRuntimeCatalog();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(calls).toEqual([]);
  });
});
