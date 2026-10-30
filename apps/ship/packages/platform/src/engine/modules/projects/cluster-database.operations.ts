import { randomBytes, createHash } from "node:crypto";
import {
  AppError,
  clusterDatabasePodCount,
  validateClusterDatabase,
  type ClusterDatabaseStep,
  type ClusterDatabaseRestoreSource,
  type ClusterDatabaseObservation,
} from "@repo/core";
import { repos, type ClusterDatabaseRecord } from "@repo/db";
import {
  ClusterDatabaseAdapter,
  clusterDatabaseHosts,
  clusterDatabaseUrl,
  type ClusterDatabaseBackupStorage,
} from "@repo/adapters";
import { ProjectDatabaseSchemas, type ClusterDatabase } from "@repo/contracts";
import type { ResourceServices } from "../../../resource-operations";
import type { ExecutionContext } from "../../../context";
import type { ProjectDependencies } from "../../../projects";
import { authorization } from "../../lib/authorization";
import { durableRunEvents } from "../../lib/durable-run-events";
import { assertResourceInOrg } from "../../lib/resource-access";
import {
  openClusterApi,
  requireClusterDeploymentTarget,
} from "../../lib/cluster-deployment-target";
import { withLiveProjectRuntimeMutation } from "../../lib/project-runtime-lock";
import { decrypt, encrypt } from "../../lib/encryption";
import { decryptSecretField } from "../../lib/credential-encryption";
import { fleetAdmin } from "../system/managed-network.operations";
import {
  assertClusterManagementAvailable,
  authorizeMember,
} from "../system/server-cluster.operations";
import {
  assertNetworkSetupAcceptingWork,
  deferNetworkSetupWork,
} from "../system/network-setup-lifecycle";
import {
  updateNetworkSetupStep,
  appendNetworkSetupLog,
  networkSetupMessage,
} from "../system/network-setup-progress";
import {
  clusterDatabaseBus,
  clusterDatabaseTopic,
  notifyClusterDatabase,
} from "./cluster-database.events";

export function presentClusterDatabase(row: ClusterDatabaseRecord): ClusterDatabase {
  return {
    id: row.id,
    projectId: row.projectId,
    clusterId: row.clusterId,
    name: row.name,
    config: row.config,
    status: row.status,
    intent: row.intent,
    sequence: row.sequence,
    generation: row.generation,
    progress: row.progress,
    observation: row.observation,
    error: row.error,
    envKey: row.envKey,
    ...clusterDatabaseHosts(row.id, row.config),
    updatedAt: row.updatedAt.toISOString(),
    createdAt: row.createdAt.toISOString(),
  };
}
async function project(ctx: ExecutionContext, id: string) {
  assertClusterManagementAvailable();
  const row = await repos.project.findById(id);
  assertResourceInOrg(row, "Project", ctx.organizationId, id);
  return row;
}
const safe = (error: unknown) =>
  networkSetupMessage(error instanceof Error ? error.message : String(error));
function retainedObservation(observation: ClusterDatabaseObservation): ClusterDatabaseObservation {
  return {
    ...observation,
    ready: false,
    message: observation.volumes.length
      ? "The database is stopped and its persistent data is retained."
      : "The database is stopped. No retained volume claims were found.",
    ...(observation.archive
      ? {
          archive: {
            ...observation.archive,
            healthy: null,
            message: "The database is stopped; scheduled backups are paused.",
          },
        }
      : {}),
  };
}

async function backupStorage(
  ctx: ExecutionContext,
  destinationId: string,
  restore?: ClusterDatabaseRestoreSource,
): Promise<ClusterDatabaseBackupStorage> {
  const destination = await repos.backupDestination.findById(destinationId);
  assertResourceInOrg(destination, "Backup destination", ctx.organizationId, destinationId);
  const accessKeyId = decryptSecretField(destination.accessKeyIdEnc),
    secretAccessKey = decryptSecretField(destination.secretAccessKeyEnc);
  if (
    destination.kind !== "s3_compatible" ||
    !destination.bucket ||
    !/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(destination.bucket) ||
    !accessKeyId ||
    !secretAccessKey
  )
    throw new AppError(
      "Choose an S3 backup destination with a bucket and access credentials.",
      422,
      "CLUSTER_DATABASE_BACKUP_DESTINATION",
    );
  if (destination.endpoint) {
    const url = new URL(destination.endpoint);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    )
      throw new AppError(
        "The S3 endpoint must be an HTTP or HTTPS address without embedded credentials or query parameters.",
        422,
        "CLUSTER_DATABASE_BACKUP_DESTINATION",
      );
  }
  const prefix = (destination.pathPrefix ?? "").replace(/^\/+|\/+$/g, "");
  if (/[\x00-\x1f\x7f]/.test(prefix) || prefix.split("/").includes(".."))
    throw new AppError(
      "The backup destination prefix contains an unsupported path.",
      422,
      "CLUSTER_DATABASE_BACKUP_DESTINATION",
    );
  const path = `s3://${destination.bucket}/${prefix ? `${prefix}/` : ""}openship/databases`;
  if (restore && (restore.destinationPath !== path || restore.endpoint !== destination.endpoint))
    throw new AppError(
      "The original backup destination address changed. Restore its saved address before recovering this database.",
      409,
      "CLUSTER_DATABASE_BACKUP_DESTINATION",
    );
  return {
    destinationId,
    accessKeyId,
    secretAccessKey,
    endpoint: destination.endpoint,
    region: destination.region || "us-east-1",
    destinationPath: restore?.destinationPath ?? path,
  };
}

export async function runClusterDatabase(
  ctx: ExecutionContext,
  row: ClusterDatabaseRecord,
  parentSignal?: AbortSignal,
) {
  const cancelled = new AbortController();
  const signal = AbortSignal.any([
    cancelled.signal,
    AbortSignal.timeout(30 * 60_000),
    ...(parentSignal ? [parentSignal] : []),
  ]);
  const progress = structuredClone(row.progress);
  const active = async () => {
    signal.throwIfAborted();
    await fleetAdmin(ctx);
    if (!(await repos.clusterDatabase.active(row.id, row.generation)))
      throw new AppError(
        "This database worker no longer owns the operation. Reload its saved progress.",
        409,
        "CLUSTER_DATABASE_EXPIRED",
      );
  };
  let writes = Promise.resolve();
  const persist = () => {
    const copy = structuredClone(progress);
    writes = writes.then(async () => {
      await repos.clusterDatabase.progress(row.id, row.generation, copy);
      notifyClusterDatabase(ctx.organizationId, row.projectId);
    });
    return writes;
  };
  let current: ClusterDatabaseStep = row.intent === "remove" ? "remove" : "connect";
  const log = async (message: string) => {
    appendNetworkSetupLog(progress, current, { message, level: "info" });
    await persist();
  };
  const step = async <T>(id: ClusterDatabaseStep, message: string, work: () => Promise<T>) => {
    current = id;
    await active();
    updateNetworkSetupStep(progress, id, "running", message);
    await persist();
    const result = await work();
    updateNetworkSetupStep(progress, id, "completed", message);
    await persist();
    return result;
  };
  let heartbeat = Promise.resolve();
  const timer = setInterval(() => {
    heartbeat = heartbeat
      .then(async () => {
        if (!signal.aborted && !(await repos.clusterDatabase.heartbeat(row.id, row.generation)))
          cancelled.abort();
      })
      .catch(() => cancelled.abort());
  }, 20_000);
  timer.unref();
  let connection: Awaited<ReturnType<typeof openClusterApi>> | undefined;
  try {
    await step(
      "connect",
      "Checking the saved cluster identity and private API connection.",
      async () => {
        const p = await project(ctx, row.projectId);
        if (p.clusterId !== row.clusterId)
          throw new Error("The project no longer targets this database's cluster.");
        connection = await openClusterApi(ctx.organizationId, row.clusterId, row.runtimeId);
        for (const host of connection.runtime.plan.hosts) await authorizeMember(ctx, host.serverId);
      },
    );
    const adapter = new ClusterDatabaseAdapter(
      connection!.api,
      {
        ...row,
        hosts: connection!.runtime.plan.hosts,
        ...(row.intent !== "remove" && row.config.backup
          ? { backupStorage: await backupStorage(ctx, row.config.backup.destinationId) }
          : {}),
        ...(row.intent === "apply" && row.restoreSource
          ? {
              restoreStorage: await backupStorage(
                ctx,
                row.restoreSource.destinationId,
                row.restoreSource,
              ),
            }
          : {}),
      },
      signal,
      active,
    );
    if (row.intent === "remove") {
      await step(
        "remove",
        row.deleteData
          ? "Removing the database and explicitly selected persistent data."
          : "Stopping the database and retaining its persistent data.",
        () => adapter.remove(row.deleteData, log),
      );
      const observation = row.deleteData ? null : retainedObservation(await adapter.observe());
      await repos.clusterDatabase.finish(
        row.id,
        row.generation,
        row.deleteData ? "deleted" : "retained",
        observation,
        null,
      );
    } else if (row.intent === "backup") {
      if (!row.backupRequestId) throw new Error("The saved database backup request is missing.");
      await step("backup", "Saving a PostgreSQL archive through the database operator.", () =>
        adapter.archive.run(row.backupRequestId!, row.generation, log),
      );
      const observation = await adapter.observe();
      await repos.clusterDatabase.finish(row.id, row.generation, "ready", observation, null);
    } else {
      await adapter.preflight();
      await step(
        "operators",
        "Preparing the pinned database operator and verifying its readiness.",
        () => adapter.operators(log),
      );
      await step("storage", "Preparing persistent storage for each database instance.", () =>
        adapter.storage(log),
      );
      await step("database", "Applying the database configuration and private access rules.", () =>
        adapter.apply(decrypt(row.secretEncrypted)),
      );
      let observation = await step(
        "verify",
        "Checking database instances, volumes and an authenticated private connection.",
        () => adapter.verify(log),
      );
      if (row.config.backup) {
        await step(
          "backup",
          "Verifying the first PostgreSQL archive at the backup destination.",
          () => adapter.archive.run("initial", row.generation, log),
        );
        observation = await adapter.observe();
      }
      await repos.clusterDatabase.finish(row.id, row.generation, "ready", observation, null);
    }
  } catch (error) {
    if (await repos.clusterDatabase.active(row.id, row.generation)) {
      const message = safe(error);
      updateNetworkSetupStep(progress, current, "failed", message);
      await persist().catch(() => {});
      await repos.clusterDatabase.finish(row.id, row.generation, "failed", null, message);
    }
  } finally {
    clearInterval(timer);
    cancelled.abort();
    await heartbeat;
    await connection?.api.dispose();
    notifyClusterDatabase(ctx.organizationId, row.projectId);
  }
}

async function queue(ctx: ExecutionContext, row: ClusterDatabaseRecord) {
  await deferNetworkSetupWork(
    {
      kind: "database",
      organizationId: ctx.organizationId,
      id: row.id,
      projectId: row.projectId,
      generation: row.generation,
    },
    (signal) => runClusterDatabase(ctx, row, signal),
  );
  notifyClusterDatabase(ctx.organizationId, row.projectId);
}
export function createClusterDatabaseOperations(
  recordAudit: ProjectDependencies["recordAudit"],
): ResourceServices<typeof ProjectDatabaseSchemas> {
  const audit = (ctx: ExecutionContext, id: string, databaseId: string, action: string) =>
    recordAudit(ctx, {
      eventType: "project.updated",
      resourceType: "project",
      resourceId: id,
      after: { action: `database.${action}`, databaseId },
    });
  const change = async (
    ctx: ExecutionContext,
    id: string,
    input: {
      databaseId: string;
      expectedSequence: number;
      config?: import("@repo/core").ClusterDatabaseConfig;
      deleteData?: boolean;
      name?: string;
    },
    action: "apply" | "retry" | "remove" | "backup",
  ) => {
    await fleetAdmin(ctx);
    assertNetworkSetupAcceptingWork();
    const result = await withLiveProjectRuntimeMutation(id, async () => {
      await project(ctx, id);
      const current = await repos.clusterDatabase.get(ctx.organizationId, id, input.databaseId);
      if (input.config?.backup) await backupStorage(ctx, input.config.backup.destinationId);
      if (action === "remove" && input.name !== current.name)
        throw new AppError(
          "Enter the database name to confirm removal.",
          422,
          "CLUSTER_DATABASE_CONFIRMATION",
        );
      const row = await repos.clusterDatabase.change(
        ctx.organizationId,
        id,
        input.databaseId,
        input.expectedSequence,
        action,
        input,
      );
      await queue(ctx, row);
      audit(ctx, id, row.id, action);
      return presentClusterDatabase(row);
    });
    if (!result) throw new AppError("The project is being removed.", 409, "PROJECT_UNAVAILABLE");
    return result;
  };
  return {
    async listClusterDatabases(ctx, id) {
      await project(ctx, id);
      return (await repos.clusterDatabase.list(ctx.organizationId, id)).map(presentClusterDatabase);
    },
    async getClusterDatabase(ctx, id, input) {
      await project(ctx, id);
      let row = await repos.clusterDatabase.get(ctx.organizationId, id, input.databaseId);
      if (input.observe && (row.status === "ready" || row.status === "retained")) {
        const connection = await openClusterApi(ctx.organizationId, row.clusterId, row.runtimeId);
        try {
          const adapter = new ClusterDatabaseAdapter(
            connection.api,
            { ...row, hosts: connection.runtime.plan.hosts },
            AbortSignal.timeout(30_000),
            async () => {
              throw new Error("Status reads cannot mutate the database.");
            },
          );
          const current = await adapter.observe();
          const observation = row.status === "retained" ? retainedObservation(current) : current;
          await repos.clusterDatabase.observe(
            ctx.organizationId,
            id,
            row.id,
            row.generation,
            observation,
          );
          row = await repos.clusterDatabase.get(ctx.organizationId, id, row.id);
        } finally {
          await connection.api.dispose();
        }
      }
      return presentClusterDatabase(row);
    },
    async createClusterDatabase(ctx, id, input) {
      await fleetAdmin(ctx);
      assertNetworkSetupAcceptingWork();
      validateClusterDatabase(input.config);
      if (
        input.config.engine === "redis" &&
        input.config.mode === "cluster" &&
        !input.clusterAwareClient
      )
        throw new AppError(
          "Redis Cluster needs a cluster-aware client. Confirm that the application supports it.",
          422,
          "CLUSTER_DATABASE_CLIENT_REQUIRED",
        );
      const result = await withLiveProjectRuntimeMutation(id, async () => {
        const p = await project(ctx, id);
        if (!p.clusterId || p.cloudWorkspaceId || p.appTemplateId === "openship")
          throw new AppError(
            "Choose a ready server cluster for this application before adding a database.",
            409,
            "CLUSTER_TARGET_REQUIRED",
          );
        const { runtime } = await requireClusterDeploymentTarget(ctx.organizationId, p.clusterId);
        for (const host of runtime.plan.hosts) await authorizeMember(ctx, host.serverId);
        const count = clusterDatabasePodCount(input.config);
        if (runtime.plan.hosts.length < count)
          throw new AppError(
            `This database needs ${count} servers so each data instance has a separate failure domain.`,
            422,
            "CLUSTER_DATABASE_CAPACITY",
          );
        if (input.config.backup) await backupStorage(ctx, input.config.backup.destinationId);
        let restoreSource: ClusterDatabaseRestoreSource | undefined;
        if (input.restoreFrom) {
          const source = await repos.clusterDatabase.get(
            ctx.organizationId,
            id,
            input.restoreFrom.databaseId,
          );
          if (
            source.config.engine !== "postgres" ||
            input.config.engine !== "postgres" ||
            !source.config.backup ||
            source.config.databaseName !== input.config.databaseName ||
            input.config.storageGiB < source.config.storageGiB ||
            source.clusterId !== p.clusterId
          )
            throw new AppError(
              "Restore a PostgreSQL backup into a new database on this cluster, using the original database name and enough storage.",
              422,
              "CLUSTER_DATABASE_RESTORE_TARGET",
            );
          const connection = await openClusterApi(
            ctx.organizationId,
            source.clusterId,
            source.runtimeId,
          );
          try {
            const adapter = new ClusterDatabaseAdapter(
              connection.api,
              { ...source, hosts: connection.runtime.plan.hosts },
              AbortSignal.timeout(30_000),
              async () => {
                throw new Error("Restore inspection cannot change the source database.");
              },
            );
            restoreSource = await adapter.archive.restoreSource(
              source.id,
              source.config.backup.destinationId,
              input.restoreFrom.backupName,
            );
            await backupStorage(ctx, restoreSource.destinationId, restoreSource);
          } finally {
            await connection.api.dispose();
          }
        }
        const { row, started } = await repos.clusterDatabase.start({
          organizationId: ctx.organizationId,
          projectId: id,
          clusterId: p.clusterId,
          runtimeId: runtime.id,
          requestId: input.requestId,
          name: input.name,
          config: input.config,
          restoreSource,
          secretEncrypted: encrypt(randomBytes(32).toString("hex")),
        });
        if (started) {
          await queue(ctx, row);
          audit(ctx, id, row.id, "created");
        }
        return presentClusterDatabase(row);
      });
      if (!result) throw new AppError("The project is being removed.", 409, "PROJECT_UNAVAILABLE");
      return result;
    },
    updateClusterDatabase: (ctx, id, input) => change(ctx, id, input, "apply"),
    retryClusterDatabase: (ctx, id, input) => change(ctx, id, input, "retry"),
    backupClusterDatabase: (ctx, id, input) => change(ctx, id, input, "backup"),
    removeClusterDatabase: (ctx, id, input) => change(ctx, id, input, "remove"),
    async connectClusterDatabase(ctx, id, input) {
      const result = await withLiveProjectRuntimeMutation(id, async () => {
        await project(ctx, id);
        const row = await repos.clusterDatabase.get(ctx.organizationId, id, input.databaseId);
        const value = input.envKey
          ? encrypt(clusterDatabaseUrl(row.id, row.config, decrypt(row.secretEncrypted)))
          : null;
        const updated = await repos.clusterDatabase.connect(
          ctx.organizationId,
          id,
          row.id,
          input.expectedSequence,
          input.envKey,
          value,
        );
        notifyClusterDatabase(ctx.organizationId, id);
        audit(ctx, id, row.id, input.envKey ? "connected" : "disconnected");
        return presentClusterDatabase(updated);
      });
      if (!result) throw new AppError("The project is being removed.", 409, "PROJECT_UNAVAILABLE");
      return result;
    },
  };
}

/** Saved snapshots over SSE; periodic server-side reads also recover expired
 * leases. Reconnection never starts an installation or repeats a mutation. */
export async function* clusterDatabaseEvents(
  ctx: ExecutionContext,
  projectId: string,
  signal?: AbortSignal,
) {
  const load = async () => {
    signal?.throwIfAborted();
    await authorization.authorize(ctx, {
      resourceType: "project",
      resourceId: projectId,
      action: "read",
    });
    await project(ctx, projectId);
    return (await repos.clusterDatabase.list(ctx.organizationId, projectId)).map(
      presentClusterDatabase,
    );
  };
  await load();
  yield* durableRunEvents({
    signal,
    load,
    subscribe: (changed) =>
      clusterDatabaseBus.subscribe(clusterDatabaseTopic(ctx.organizationId, projectId), changed),
    version: (rows) =>
      createHash("sha256")
        .update(JSON.stringify(rows.map((row) => [row.id, row.sequence])))
        .digest("hex"),
    complete: () => false,
  });
}
