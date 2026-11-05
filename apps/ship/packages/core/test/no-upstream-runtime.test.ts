// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// FreeBuild must not depend on the upstream project's servers or repositories at
// runtime (DR-0018 hard line 5). These cases fail if a default configuration can
// reach github.com/oblien, raw.githubusercontent.com/oblien, ghcr.io/oblien or
// opsh.io, either through a default image reference or through source that fetches
// from them.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import {
  APP_TEMPLATES,
  DEFAULT_IMAGE_REGISTRY,
  MANAGED_IMAGE_NAMES,
  SYSTEM,
  buildEdgeImageRef,
  buildMailImageRef,
  isUpstreamRepo,
  isUpstreamUrl,
} from "../src";
import { GITHUB_REPO, RELEASES_LATEST_API, advisoryManifestUrl } from "../src/updates/types";
import { changelogMarkdownUrl } from "../src/updates/changelog";

const SHIP_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const UPSTREAM = /oblien|openship\.io|opsh\.io|ghcr\.io\/openship/i;

describe("upstream URL and repo matching", () => {
  it("recognises the upstream hosts and repositories", () => {
    for (const url of [
      "https://raw.githubusercontent.com/oblien/openship/main/packages/core/src/apps/catalog.json",
      "https://raw.githubusercontent.com/openship/native-modules/main/modules/x/catalog.json",
      "https://api.github.com/repos/oblien/openship/releases/latest",
      "https://github.com/oblien/openship/releases/download/v1/x.tar.gz",
      "https://ghcr.io/oblien/openship-api",
      "https://api.oblien.com/v1",
      "https://app.openship.io/x",
      "https://edge.opsh.io",
    ]) {
      expect(isUpstreamUrl(url), url).toBe(true);
    }
    expect(isUpstreamRepo("oblien/openship")).toBe(true);
    expect(isUpstreamRepo("Openship/native-modules")).toBe(true);
  });

  it("leaves operator-controlled sources alone", () => {
    for (const url of [
      "https://raw.githubusercontent.com/example-org/catalog/main/catalog.json",
      "https://github.com/Netie-AI/openvault",
      "https://mirror.example.com/GeoLite2-Country.mmdb",
      "http://127.0.0.1:9000/catalog.json",
      "not a url",
    ]) {
      expect(isUpstreamUrl(url), url).toBe(false);
    }
    expect(isUpstreamRepo("Netie-AI/openvault")).toBe(false);
    expect(isUpstreamRepo("")).toBe(false);
  });
});

describe("default image references", () => {
  const saved = { ...process.env };
  afterEach(() => {
    process.env = { ...saved };
  });

  it("resolve to local FreeBuild names, never the upstream registry", () => {
    for (const key of [
      "OPENSHIP_IMAGE_REGISTRY",
      "OPENSHIP_VERSION",
      "OPENSHIP_EDGE_IMAGE",
      "OPENSHIP_EDGE_TAG",
      "OPENSHIP_MAIL_IMAGE",
      "OPENSHIP_MAIL_TAG",
    ]) {
      delete process.env[key];
    }
    expect(DEFAULT_IMAGE_REGISTRY).toBe("localhost/freebuild");
    expect(buildEdgeImageRef()).toBe("localhost/freebuild/freebuild-edge:latest");
    expect(buildMailImageRef()).toBe("localhost/freebuild/freebuild-mail:latest");
    for (const ref of [buildEdgeImageRef(), buildMailImageRef()]) {
      expect(ref).not.toMatch(UPSTREAM);
      expect(ref).not.toMatch(/^ghcr\.io\//);
    }
  });

  it("use the managed FreeBuild image names", () => {
    expect(MANAGED_IMAGE_NAMES).toEqual({
      api: "freebuild-api",
      dashboard: "freebuild-dashboard",
      edge: "freebuild-edge",
      mail: "freebuild-mail",
      webmail: "freebuild-webmail",
    });
  });

  it("the webmail catalog app points at the local FreeBuild image", () => {
    const webmail = APP_TEMPLATES.find((app) => app.id === "webmail");
    const images = JSON.stringify(webmail);
    expect(images).toContain(`${DEFAULT_IMAGE_REGISTRY}/${MANAGED_IMAGE_NAMES.webmail}:latest`);
    expect(images).not.toMatch(/ghcr\.io\/oblien/);
  });

  it("no bundled catalog app other than the named third-party exception pulls from the upstream registry", () => {
    const offenders: string[] = [];
    for (const app of APP_TEMPLATES) {
      // mindwire is a third-party app the operator chooses to install (DR-0018 open item).
      if (app.id === "mindwire") continue;
      for (const match of JSON.stringify(app).matchAll(/"image":"([^"]+)"/g)) {
        if (/oblien|openship/i.test(match[1]!)) offenders.push(`${app.id}: ${match[1]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("no catalog app pre-fills an upstream address", () => {
    expect(JSON.stringify(APP_TEMPLATES)).not.toContain("@openship.local");
  });
});

describe("the shipped compose stack", () => {
  const compose = readFileSync(join(SHIP_ROOT, "docker", "docker-compose.yml"), "utf8");

  it("builds api, dashboard and edge from this repository and pulls nothing from the upstream", () => {
    expect(compose).not.toMatch(/ghcr\.io\/oblien/);
    expect(compose).not.toMatch(/:-ghcr\.io/);
    for (const [service, dockerfile] of [
      ["api", "apps/api/Dockerfile"],
      ["dashboard", "apps/dashboard/Dockerfile"],
      ["edge", "apps/edge/Dockerfile"],
    ] as const) {
      expect(compose).toContain(`/freebuild-${service}:`);
      expect(compose).toContain(`dockerfile: ${dockerfile}`);
      expect(existsSync(join(SHIP_ROOT, dockerfile)), dockerfile).toBe(true);
    }
  });

  it("defaults to the FreeBuild ports 3030 and 3031", () => {
    expect(compose).not.toMatch(/API_PORT:-4000|DASHBOARD_PORT:-3001/);
    expect(compose).toContain("API_PORT:-3030");
    expect(compose).toContain("DASHBOARD_PORT:-3031");
  });
});

describe("release feed", () => {
  it("has no default repository, so no request can name the upstream project", () => {
    expect(GITHUB_REPO).toBe("");
    expect(RELEASES_LATEST_API).toBe("");
    expect(advisoryManifestUrl("v1.0.0")).toBe("");
    expect(changelogMarkdownUrl("v1.0.0")).toBe("");
  });
});

describe("DNS ownership record", () => {
  it("is emitted under the FreeBuild name; the legacy name stays recognised", () => {
    expect(SYSTEM.DOMAINS.VERIFICATION_PREFIX).toBe("_freebuild-challenge");
    expect(SYSTEM.DOMAINS.LEGACY_VERIFICATION_PREFIX).toBe("_openship-challenge");
  });
});

/** Source that could run in a default install, scanned for fetch targets on the upstream. */
describe("shipped source", () => {
  const FETCH_TARGET =
    /raw\.githubusercontent\.com\/(oblien|openship)\b|api\.github\.com\/repos\/(oblien|openship)\b|ghcr\.io\/oblien\b|github\.com\/(oblien|openship)\/[\w.-]+\/releases\/download|["'`]oblien\/openship["'`]/;
  /** Allowed because the operator picks these apps; listed in DR-0018 as open. */
  const ALLOWED = new Set([
    "packages/core/src/apps/catalog.json",
    "packages/core/src/apps/catalog/mindwire.json",
  ]);

  function walk(dir: string, out: string[]): void {
    for (const name of readdirSync(dir)) {
      if (["node_modules", "dist", ".next", ".turbo", "test", "tests", "e2e", "__tests__"].includes(name)) continue;
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path, out);
      else if (/\.(ts|tsx|mjs|json|yml|yaml)$/.test(name) && !/\.test\./.test(name)) out.push(path);
    }
  }

  it("names no upstream fetch target outside comments", () => {
    const files: string[] = [];
    for (const area of ["apps", "packages"]) {
      for (const pkg of readdirSync(join(SHIP_ROOT, area))) {
        const src = join(SHIP_ROOT, area, pkg, "src");
        if (existsSync(src)) walk(src, files);
      }
    }
    walk(join(SHIP_ROOT, "docker"), files);
    const offenders: string[] = [];
    for (const file of files) {
      const rel = relative(SHIP_ROOT, file);
      if (ALLOWED.has(rel)) continue;
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, index) => {
          const trimmed = line.trim();
          if (/^(\/\/|\/\*|\*|#)/.test(trimmed)) return;
          if (FETCH_TARGET.test(line)) offenders.push(`${rel}:${index + 1}: ${trimmed.slice(0, 120)}`);
        });
    }
    expect(offenders).toEqual([]);
  });
});
