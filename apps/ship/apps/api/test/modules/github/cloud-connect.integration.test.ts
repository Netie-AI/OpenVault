import { beforeEach, describe, expect, it, vi } from "vitest";
import { Hono } from "hono";

const github = vi.hoisted(() => ({
  userStatus: vi.fn(),
  userToken: vi.fn(),
  fetch: vi.fn(),
  appFetch: vi.fn(),
}));

vi.mock("@repo/platform/engine/config/env", async (original) => {
  const config = await original<typeof import("@repo/platform/engine/config/env")>();
  return { ...config, env: { ...config.env, CLOUD_MODE: true, GITHUB_APP_ID: "9" } };
});
vi.mock("@repo/platform/engine/modules/github/github.auth", async (original) => ({
  ...(await original<object>()),
  getUserStatus: github.userStatus,
  getUserToken: github.userToken,
  appFetch: github.appFetch,
}));
vi.mock("@repo/platform/engine/modules/github/github.http", async (original) => ({
  ...(await original<object>()),
  ghFetch: github.fetch,
}));

import {
  db,
  schema,
  repos,
  seedOwner,
  installFakeRunner,
  type SeededOwner,
} from "../jobs/_harness";
import { eq } from "@repo/db";
import { getPlatformKernel } from "@repo/platform/engine/lib/platform";
import { createShip } from "@repo/sdk/native";
import { OpenshipClient } from "@repo/sdk/client";
import { githubRoutes } from "../../../src/modules/github/github.routes";
import { healthRoutes } from "../../../src/modules/health/health.routes";
import { githubInstallCallback } from "../../../src/modules/cloud/cloud-saas.controller";
import { handleApiError } from "../../../src/middleware/error-handler";

installFakeRunner();
const app = new Hono()
  .onError(handleApiError)
  .route("/api/health", healthRoutes)
  .route("/api/github", githubRoutes)
  .get("/api/cloud/github/install-callback", githubInstallCallback);

const installation = {
  id: 42,
  account: { login: "Acme", id: 700, avatar_url: "", type: "Organization" },
  app_id: 9,
  target_type: "Organization",
  permissions: {},
  events: [],
};

async function clients(actor: SeededOwner) {
  const user = (await repos.user.findById(actor.userId))!;
  const ship = createShip({
    platform: getPlatformKernel(),
    identity: { resolve: async () => ({ user, sessionId: "cloud-github-test" }) },
  });
  return {
    native: await ship.scope({ identity: "verified", organizationId: actor.orgId }),
    http: new OpenshipClient({
      baseUrl: "https://api.openship.test",
      token: actor.token,
      organizationId: actor.orgId,
      fetch: ((url, init) => app.request(url as string, init)) as typeof fetch,
    }),
  };
}

async function start(actor: SeededOwner) {
  const c = await clients(actor);
  const result = await c.http.github.connect();
  if (result.connected || result.flow !== "redirect" || !result.state || !result.url) {
    throw new Error("Expected a state-bound GitHub installation redirect");
  }
  expect(result.step).toBe("install");
  expect(new URL(result.url).searchParams.get("state")).toBe(result.state);
  return { ...c, state: result.state };
}

function callback(state: string, setupAction = "install") {
  return app.request(
    `https://api.openship.test/api/cloud/github/install-callback?${new URLSearchParams({
      state,
      installation_id: "42",
      setup_action: setupAction,
    })}`,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  github.userStatus.mockResolvedValue({
    connected: true,
    tokenSource: "oauth",
    login: "installer",
    id: 17,
    avatar_url: "",
  });
  github.userToken.mockResolvedValue("github-user-token");
  github.fetch.mockResolvedValue({ total_count: 1, installations: [installation] });
  github.appFetch.mockResolvedValue(installation);
});

describe("Cloud GitHub installation through HTTP, shared engine, and database", () => {
  it("reads status without minting install nonces, while preserving the normal install URL response", async () => {
    const actor = await seedOwner();
    const c = await clients(actor);
    const states = () =>
      db.query.githubInstallState.findMany({
        where: eq(schema.githubInstallState.organizationId, actor.orgId),
      });
    for (const client of [c.http, c.native]) {
      await client.github.getStatus({ includeInstallUrl: false });
      expect(await states()).toEqual([]);
    }
    const status = await c.http.github.getStatus();
    const issued = await states();
    expect(issued).toHaveLength(1);
    expect(new URL(status.installUrl).searchParams.get("state")).toBe(issued[0].state);
    expect((await app.request("/api/github/status?includeInstallUrl=false")).status).toBe(401);
  });

  it("claims the installation without a popup session and exposes it to the correct workspace", async () => {
    const actor = await seedOwner();
    const c = await start(actor);
    expect(await c.http.github.getStatus({ includeInstallUrl: false })).toMatchObject({
      state: { sources: { openshipApp: { connected: true, hasInstallations: false } } },
      accounts: [],
      installUrl: "",
    });
    expect(await repos.githubInstallState.find(c.state)).toMatchObject({
      userId: actor.userId,
      organizationId: actor.orgId,
    });

    // GitHub returns here without Openship cookies or a bearer credential.
    const response = await callback(c.state);
    expect(response.status).toBe(200);
    expect(await response.text()).toContain("GitHub App installed");
    expect(await repos.githubInstallState.find(c.state)).toBeFalsy();
    expect(await repos.gitInstallation.listByOrganization(actor.orgId)).toMatchObject([
      { installationId: 42, owner: "acme", userId: actor.userId },
    ]);

    for (const client of [c.http, c.native]) {
      expect(await client.github.getStatus({ includeInstallUrl: false })).toMatchObject({
        state: { sources: { openshipApp: { connected: true, hasInstallations: true } } },
        accounts: [{ login: "acme", source: "app" }],
        installUrl: "",
      });
      expect(await client.github.connect({ source: "oauth" })).toEqual({ connected: true });
    }
    const other = await clients(await seedOwner());
    expect(await other.http.github.getStatus({ includeInstallUrl: false })).toMatchObject({
      accounts: [],
    });
    expect((await callback(c.state)).status).toBe(400);
  });

  it("does not claim a failed verification and can retry the same callback after GitHub recovers", async () => {
    const actor = await seedOwner();
    const c = await start(actor);
    github.appFetch.mockRejectedValueOnce(new Error("GitHub temporarily unavailable"));
    const failed = await callback(c.state);
    expect(failed.status).toBe(500);
    expect(await failed.text()).toContain("GitHub temporarily unavailable");
    expect(await repos.gitInstallation.listByOrganization(actor.orgId)).toEqual([]);
    expect(await repos.githubInstallState.find(c.state)).toBeTruthy();
    expect(await c.http.github.getStatus({ includeInstallUrl: false })).toMatchObject({
      state: { sources: { openshipApp: { hasInstallations: false } } },
    });

    expect((await callback(c.state)).status).toBe(200);
    expect(await c.http.github.getStatus({ includeInstallUrl: false })).toMatchObject({
      state: { sources: { openshipApp: { hasInstallations: true } } },
    });
  });

  it("keeps approval requests and a different App's installation out of connected workspace access", async () => {
    const actor = await seedOwner();
    const c = await start(actor);
    github.appFetch.mockResolvedValueOnce({ ...installation, app_id: 10 });
    expect((await callback(c.state)).status).toBe(403);
    expect(await repos.gitInstallation.listByOrganization(actor.orgId)).toEqual([]);

    const pending = await callback(c.state, "request");
    expect(pending.status).toBe(200);
    expect(await pending.text()).toContain("requested");
    expect(await c.http.github.getStatus({ includeInstallUrl: false })).toMatchObject({
      accounts: [],
      state: { sources: { openshipApp: { hasInstallations: false } } },
    });
  });
});
