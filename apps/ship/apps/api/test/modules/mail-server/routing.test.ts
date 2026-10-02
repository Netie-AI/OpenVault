/**
 * Mail-server routing - pure builder tests.
 *
 * Covers `buildMailServerRoutes` only - the registration service hits
 * openship's platform() and is best validated with an integration test
 * once the provisioning module exists.
 */

import { describe, it, expect } from "vitest";
import { buildMailServerRoutes, type MailServerRouteInput } from "@repo/core";

const baseInput: MailServerRouteInput = {
  userDomain: "acme.com",
  mailServerIp: "203.0.113.10",
  zeroServerOrigin: "https://zero-server.internal:3001",
  zeroClientOrigin: "https://zero-client.opsh.io",
  openshipApiOrigin: "https://api.opsh.io",
};

describe("buildMailServerRoutes", () => {
  it("emits exactly the three user-facing HTTP routes (no admin subdomain)", () => {
    // Admin operations run inside openship's own API writing to the mail-server
    // Postgres directly via @repo/db-email - no public admin endpoint exists.
    const plan = buildMailServerRoutes(baseInput);
    expect(plan.routes.map((r) => r.id)).toEqual([
      "mail-client",
      "mail-api",
      "autodiscover",
    ]);
  });

  it("uses the exact hostnames the architecture doc declares", () => {
    const plan = buildMailServerRoutes(baseInput);
    const byId = Object.fromEntries(plan.routes.map((r) => [r.id, r.hostname]));
    expect(byId["mail-client"]).toBe("mail.acme.com");
    expect(byId["mail-api"]).toBe("api.mail.acme.com");
    expect(byId["autodiscover"]).toBe("autodiscover.acme.com");
  });

  it("proxies each route to the right backend origin", () => {
    const plan = buildMailServerRoutes(baseInput);
    const byId = Object.fromEntries(plan.routes.map((r) => [r.id, r.targetUrl]));
    expect(byId["mail-client"]).toBe(baseInput.zeroClientOrigin);
    expect(byId["mail-api"]).toBe(baseInput.zeroServerOrigin);
    expect(byId["autodiscover"]).toBe(baseInput.openshipApiOrigin);
  });

  it("does not emit any public admin subdomain (admin is openship-internal)", () => {
    const plan = buildMailServerRoutes(baseInput);
    expect(plan.routes.find((r) => r.hostname.startsWith("email-admin"))).toBeUndefined();
    expect(plan.dns.find((r) => r.name.startsWith("email-admin"))).toBeUndefined();
  });

  it("marks every route as TLS-required (mail surfaces are never plaintext)", () => {
    const plan = buildMailServerRoutes(baseInput);
    expect(plan.routes.every((r) => r.tls === true)).toBe(true);
  });

  // ── DNS ────────────────────────────────────────────────────────────────

  it("emits the apex MX + A records required for mail delivery", () => {
    const plan = buildMailServerRoutes(baseInput);
    const byId = Object.fromEntries(plan.dns.map((r) => [r.id, r]));

    expect(byId["mailservice-a"]).toMatchObject({
      type: "A",
      name: "mailservice.acme.com",
      value: "203.0.113.10",
      required: true,
    });

    expect(byId["apex-mx"]).toMatchObject({
      type: "MX",
      name: "acme.com",
      value: "mailservice.acme.com.",
      priority: 10,
      required: true,
    });
  });

  it("emits SPF / DKIM / DMARC as recommended (not blocking) records", () => {
    const plan = buildMailServerRoutes(baseInput);
    const byId = Object.fromEntries(plan.dns.map((r) => [r.id, r]));

    expect(byId["spf"]).toMatchObject({
      type: "TXT",
      name: "acme.com",
      value: "v=spf1 mx -all",
      required: false,
    });

    expect(byId["dkim"]).toMatchObject({
      type: "TXT",
      name: "dkim._domainkey.acme.com",
      required: false,
    });
    // DKIM is a placeholder - the value comes from the post-install Amavisd key.
    expect(byId["dkim"]?.value).toContain("DKIM");

    expect(byId["dmarc"]).toMatchObject({
      type: "TXT",
      name: "_dmarc.acme.com",
      required: false,
    });
    expect(byId["dmarc"]?.value).toMatch(/^v=DMARC1;/);
  });

  it("emits CNAMEs from the user-facing hostnames to the routing-layer ingresses", () => {
    const plan = buildMailServerRoutes(baseInput);
    const byId = Object.fromEntries(plan.dns.map((r) => [r.id, r]));

    expect(byId["mail-client-cname"]).toMatchObject({
      type: "CNAME",
      name: "mail.acme.com",
      value: "zero-client.opsh.io",
      required: true,
    });
    expect(byId["mail-api-cname"]).toMatchObject({
      type: "CNAME",
      name: "api.mail.acme.com",
      value: "zero-server.internal",
      required: true,
    });
    expect(byId["autodiscover-cname"]).toMatchObject({
      type: "CNAME",
      name: "autodiscover.acme.com",
      value: "api.opsh.io",
      required: false,
    });
  });

  // ── Input normalization ────────────────────────────────────────────────

  it("normalizes the user domain (lower-case, trims dots and whitespace)", () => {
    const plan = buildMailServerRoutes({
      ...baseInput,
      userDomain: "  ACME.com.  ",
    });
    expect(plan.input.userDomain).toBe("acme.com");
    expect(plan.routes[0].hostname).toBe("mail.acme.com");
    expect(plan.dns.find((r) => r.id === "apex-mx")?.name).toBe("acme.com");
  });

  it("strips scheme + port from origins when emitting CNAMEs (hostnames only)", () => {
    const plan = buildMailServerRoutes({
      ...baseInput,
      zeroServerOrigin: "https://mail-vps-1.internal:3001/some/path",
      zeroClientOrigin: "https://zero-client.opsh.io:443",
    });
    expect(
      plan.dns.find((r) => r.id === "mail-api-cname")?.value,
    ).toBe("mail-vps-1.internal");
    expect(
      plan.dns.find((r) => r.id === "mail-client-cname")?.value,
    ).toBe("zero-client.opsh.io");
  });

  // ── Determinism ────────────────────────────────────────────────────────

  it("is pure - same input produces deeply-equal output", () => {
    const a = buildMailServerRoutes(baseInput);
    const b = buildMailServerRoutes(baseInput);
    expect(a).toEqual(b);
  });

  it("re-running with a new mail VPS IP changes only mailservice A + the input", () => {
    const before = buildMailServerRoutes(baseInput);
    const after = buildMailServerRoutes({ ...baseInput, mailServerIp: "203.0.113.99" });

    // Routes are unchanged - they don't include the raw IP.
    expect(after.routes).toEqual(before.routes);

    // Only the mailservice-a record's value differs in DNS.
    const beforeMailservice = before.dns.find((r) => r.id === "mailservice-a");
    const afterMailservice = after.dns.find((r) => r.id === "mailservice-a");
    expect(afterMailservice?.value).toBe("203.0.113.99");
    expect(beforeMailservice?.value).toBe("203.0.113.10");

    // Every other DNS record is identical.
    const beforeOther = before.dns.filter((r) => r.id !== "mailservice-a");
    const afterOther = after.dns.filter((r) => r.id !== "mailservice-a");
    expect(afterOther).toEqual(beforeOther);
  });
});
