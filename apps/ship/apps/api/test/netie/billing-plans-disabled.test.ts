// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// GET /api/billing/plans served the upstream hosted cloud's plan catalog (its
// provider, its free-subdomain suffix and its sales contact) with no credentials.
// It now answers the same 501 hosted_cloud_disabled as every other billing path.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Hono } from "hono";
import { describe, expect, it } from "vitest";

import { handleApiError } from "../../src/middleware/error-handler";
import { hostedCloudDisabledRoutes } from "../../src/modules/netie/hosted-cloud-disabled.routes";

describe("GET /api/billing/plans", () => {
  it("is not mounted by app.ts, so it falls through to hosted_cloud_disabled", () => {
    const app = readFileSync(join(__dirname, "../../src/app.ts"), "utf8");
    const code = app
      .split("\n")
      .filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line))
      .join("\n");
    expect(code).not.toMatch(/billingPlansRoutes/);
  });

  it("answers 501 hosted_cloud_disabled with no upstream names in the body", async () => {
    const app = new Hono();
    app.onError(handleApiError);
    app.route("/api/billing", hostedCloudDisabledRoutes);

    const res = await app.request("/api/billing/plans");
    expect(res.status).toBe(501);
    const text = await res.text();
    expect(JSON.parse(text).error.code).toBe("hosted_cloud_disabled");
    expect(text).not.toMatch(/oblien|opsh|openship/i);
  });
});
