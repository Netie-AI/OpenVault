import { afterEach, describe, expect, it, vi } from "vitest";
import { Hono } from "hono";
import { db, schema, repos, seedOwner, seedServer, type SeededOwner } from "../jobs/_harness";
import { createShip } from "@repo/sdk/native";
import { OpenshipClient } from "@repo/sdk/client";
import { getPlatformKernel } from "@repo/platform/engine/lib/platform";
import { sshManager } from "@repo/platform/engine/lib/ssh-manager";
import * as lifecycle from "@repo/platform/engine/modules/system/network-setup-lifecycle";
import { projectRoutes } from "../../../src/modules/projects/project.routes";
import { healthRoutes } from "../../../src/modules/health/health.routes";
import { handleApiError } from "../../../src/middleware/error-handler";
import { clusterRuntimePlanFixture } from "../../../../../packages/contracts/test/cluster-runtime-fixtures";
import type { ClusterDatabaseObservation } from "@repo/core";

const app = new Hono()
  .onError(handleApiError)
  .route("/api/health", healthRoutes)
  .route("/api/projects", projectRoutes);
async function clients(owner: SeededOwner) {
  const user = (await repos.user.findById(owner.userId))!;
  const ship = createShip({
    platform: getPlatformKernel(),
    identity: {
      resolve: async () => ({
        user: { id: user.id, email: user.email, name: user.name },
        sessionId: "database-test",
      }),
    },
  });
  const native = await ship.scope({ identity: "verified", organizationId: owner.orgId });
  const remote = new OpenshipClient({
    baseUrl: "http://openship.test",
    token: owner.token,
    organizationId: owner.orgId,
    fetch: ((url, init) => app.request(url as string, init)) as typeof fetch,
  });
  return { native: native.projects, remote: remote.projects };
}
async function project(owner: SeededOwner) {
  const plan = clusterRuntimePlanFixture();
  for (const host of plan.hosts) {
    host.serverId = await seedServer(owner.orgId, host.name);
    host.ready = true;
    host.installed = true;
    for (const step of host.steps) step.status = "completed";
  }
  const network = await repos.serverCluster.create(
    owner.orgId,
    {
      name: "Private network",
      network: { mode: "native", cidrs: ["10.20.0.0/24"], mtu: 1400, probePort: 51821 },
      members: plan.hosts.map((host) => ({
        serverId: host.serverId,
        privateIp: host.privateIp,
        providerId: "custom" as const,
      })),
    },
    crypto.randomUUID(),
    "network",
  );
  const cluster = await repos.computeCluster.create(
    owner.orgId,
    { name: "Pool", networkId: network.id, serverIds: plan.hosts.map((host) => host.serverId) },
    crypto.randomUUID(),
    "cluster",
  );
  plan.networkId = network.id;
  plan.clusterUid = "test-kubernetes-uid";
  const { row } = await repos.clusterRuntime.start(
    owner.orgId,
    cluster.id,
    cluster.revision,
    crypto.randomUUID(),
    plan,
  );
  await repos.clusterRuntime.finish(row.id, row.generation, plan, "setup", null);
  const id = crypto.randomUUID(),
    groupId = crypto.randomUUID();
  await db
    .insert(schema.projectGroup)
    .values({ id: groupId, organizationId: owner.orgId, name: "API", slug: id });
  await db
    .insert(schema.project)
    .values({
      id,
      groupId,
      organizationId: owner.orgId,
      name: "API",
      slug: id,
      clusterId: cluster.id,
      clusterConfig: { replicas: 1 },
    });
  return { id, clusterId: cluster.id };
}
afterEach(() => vi.restoreAllMocks());

describe("project databases through real HTTP and native operations", () => {
  it("shares idempotent setup, progress, connection ownership and retry without leaking credentials", async () => {
    const hostWork = vi
      .spyOn(sshManager, "withExecutor")
      .mockRejectedValue(new Error("Unexpected host work"));
    const dispatch = vi.spyOn(lifecycle, "deferNetworkSetupWork").mockResolvedValue(undefined);
    const owner = await seedOwner(),
      target = await project(owner);
    const { native, remote } = await clients(owner);
    const input = {
      requestId: crypto.randomUUID(),
      name: "postgres",
      config: {
        engine: "postgres" as const,
        mode: "standalone" as const,
        instances: 1,
        storageClass: "openship-local",
        storageGiB: 20,
        cpuMillis: 500,
        memoryMiB: 512,
        databaseName: "app",
      },
    };
    const created = await remote.createClusterDatabase(target.id, input);
    expect(created).toMatchObject({
      status: "provisioning",
      generation: 1,
      clusterId: target.clusterId,
    });
    expect(await native.createClusterDatabase(target.id, input)).toEqual(created);
    expect(dispatch).toHaveBeenCalledOnce();
    expect(dispatch.mock.calls[0]?.[0]).toMatchObject({
      kind: "database",
      id: created.id,
      projectId: target.id,
    });
    for (const client of [native, remote]) {
      expect(await client.listClusterDatabases(target.id)).toEqual([created]);
      const controller = new AbortController();
      const events = client
        .streamClusterDatabaseEvents(target.id, { signal: controller.signal })
        [Symbol.asyncIterator]();
      try {
        const event = await events.next();
        const snapshot = JSON.parse(event.value!.data);
        expect(snapshot.run).toEqual([created]);
        expect(event.value!.data).not.toMatch(/secretEncrypted|envValueEncrypted|leaseExpiresAt/);
      } finally {
        controller.abort();
        await events.return?.();
      }
    }
    const other = await clients(await seedOwner());
    for (const client of [other.native, other.remote])
      await expect(
        client.getClusterDatabase(target.id, { databaseId: created.id }),
      ).rejects.toMatchObject({ code: "NOT_FOUND" });
    const observation: ClusterDatabaseObservation = {
      ready: true,
      message: "Verified in the adapter acceptance suite",
      observedAt: new Date().toISOString(),
      primary: "database-1",
      pods: [],
      volumes: [],
    };
    await repos.clusterDatabase.finish(created.id, 1, "ready", observation, null);
    const ready = await remote.getClusterDatabase(target.id, { databaseId: created.id });
    await expect(
      native.connectClusterDatabase(target.id, {
        databaseId: created.id,
        expectedSequence: created.sequence,
        envKey: "DATABASE_URL",
      }),
    ).rejects.toMatchObject({ code: "CLUSTER_DATABASE_CONFLICT" });
    const connected = await native.connectClusterDatabase(target.id, {
      databaseId: created.id,
      expectedSequence: ready.sequence,
      envKey: "DATABASE_URL",
    });
    expect(connected.envKey).toBe("DATABASE_URL");
    const variables = await repos.project.getEnvMap(target.id, "production");
    expect(variables.DATABASE_URL).toBeTruthy();
    expect(variables.DATABASE_URL).not.toContain("postgresql://");
    await expect(
      remote.removeClusterDatabase(target.id, {
        databaseId: created.id,
        expectedSequence: connected.sequence,
        name: connected.name,
        deleteData: false,
      }),
    ).rejects.toMatchObject({ code: "CLUSTER_DATABASE_CONFLICT" });
    const disconnected = await remote.connectClusterDatabase(target.id, {
      databaseId: created.id,
      expectedSequence: connected.sequence,
      envKey: null,
    });
    expect((await repos.project.getEnvMap(target.id, "production")).DATABASE_URL).toBeUndefined();
    const removing = await remote.removeClusterDatabase(target.id, {
      databaseId: created.id,
      expectedSequence: disconnected.sequence,
      name: created.name,
      deleteData: false,
    });
    await repos.clusterDatabase.finish(
      created.id,
      removing.generation,
      "failed",
      null,
      "Host unavailable",
    );
    const failed = await native.getClusterDatabase(target.id, { databaseId: created.id });
    const retried = await native.retryClusterDatabase(target.id, {
      databaseId: created.id,
      expectedSequence: failed.sequence,
    });
    expect(retried).toMatchObject({
      intent: "remove",
      status: "deleting",
      generation: removing.generation + 1,
    });
    expect(hostWork).not.toHaveBeenCalled();
  });
});
