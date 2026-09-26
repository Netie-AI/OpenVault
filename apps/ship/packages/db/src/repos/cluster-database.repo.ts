import { and, eq, gt, inArray, isNull, lte, ne, or, sql, type SQL } from "drizzle-orm";
import {
  AppError,
  NotFoundError,
  CLUSTER_DATABASE_LEASE_MS,
  clusterDatabaseRunning,
  validateClusterDatabase,
  validateClusterDatabaseUpdate,
  type ClusterDatabaseConfig,
  type ClusterDatabaseProgress,
  type ClusterDatabaseObservation,
  type ClusterDatabaseStatus,
} from "@repo/core";
import type { Database } from "../client";
import {
  clusterDatabase as table,
  clusterRuntime,
  project,
  envVar,
  backupDestination,
} from "../schema";
import { isDeepStrictEqual } from "node:util";

export type ClusterDatabaseRecord = typeof table.$inferSelect;
const conflict = (message: string) => new AppError(message, 409, "CLUSTER_DATABASE_CONFLICT");
type Transaction = Parameters<Parameters<Database["transaction"]>[0]>[0];
async function lockDestinations(tx: Transaction, org: string, ids: Array<string | undefined>) {
  for (const id of [...new Set(ids.filter((id): id is string => !!id))].sort()) {
    const [destination] = await tx
      .select()
      .from(backupDestination)
      .where(
        and(
          eq(backupDestination.id, id),
          eq(backupDestination.organizationId, org),
          isNull(backupDestination.deletedAt),
        ),
      )
      .for("update");
    if (
      !destination ||
      destination.kind !== "s3_compatible" ||
      !destination.bucket ||
      !destination.accessKeyIdEnc ||
      !destination.secretAccessKeyEnc
    )
      throw conflict("Choose an available S3 backup destination with access credentials.");
  }
}
export function createClusterDatabaseRepo(db: Database) {
  const owned = (org: string, projectId: string) =>
    and(eq(table.organizationId, org), eq(table.projectId, projectId));
  const worker = (id: string, generation: number) =>
    and(
      eq(table.id, id),
      eq(table.generation, generation),
      inArray(table.status, ["provisioning", "deleting"]),
      gt(table.leaseExpiresAt, new Date()),
    );
  const expired = () => or(isNull(table.leaseExpiresAt), lte(table.leaseExpiresAt, new Date()));
  async function interrupt(where: SQL | undefined, message: string) {
    return db
      .update(table)
      .set({
        status: "interrupted",
        error: message,
        leaseExpiresAt: null,
        sequence: sql`${table.sequence} + 1`,
        updatedAt: new Date(),
      })
      .where(and(inArray(table.status, ["provisioning", "deleting"]), where))
      .returning();
  }
  async function expire(org: string) {
    await interrupt(
      and(eq(table.organizationId, org), expired()),
      "OpenShip stopped reporting database setup progress. Retry to inspect the existing resources and continue.",
    );
  }
  async function get(org: string, projectId: string, id: string) {
    await expire(org);
    const [row] = await db
      .select()
      .from(table)
      .where(and(owned(org, projectId), eq(table.id, id)));
    if (!row) throw new NotFoundError("Database", id);
    return row;
  }
  return {
    get,
    async list(org: string, projectId: string) {
      await expire(org);
      return db
        .select()
        .from(table)
        .where(and(owned(org, projectId), ne(table.status, "deleted")))
        .orderBy(table.createdAt);
    },
    async start(
      input: Pick<
        ClusterDatabaseRecord,
        | "organizationId"
        | "projectId"
        | "clusterId"
        | "runtimeId"
        | "requestId"
        | "name"
        | "config"
        | "secretEncrypted"
      > &
        Partial<Pick<ClusterDatabaseRecord, "restoreSource">>,
    ) {
      validateClusterDatabase(input.config);
      return db.transaction(async (tx) => {
        // Serialize with cluster disable and project target changes. The project
        // runtime lock is also held by the caller, including deletion admission.
        const [runtime] = await tx
          .select()
          .from(clusterRuntime)
          .where(
            and(
              eq(clusterRuntime.id, input.runtimeId),
              eq(clusterRuntime.organizationId, input.organizationId),
            ),
          )
          .for("update");
        if (!runtime || runtime.status !== "ready" || runtime.clusterId !== input.clusterId)
          throw conflict("The server cluster is no longer ready.");
        const [p] = await tx
          .select()
          .from(project)
          .where(
            and(eq(project.id, input.projectId), eq(project.organizationId, input.organizationId)),
          )
          .for("update");
        if (!p || p.deletionInProgress || p.clusterId !== input.clusterId || p.cloudWorkspaceId)
          throw conflict("The project's cluster changed. Reload before adding a database.");
        const [existing] = await tx
          .select()
          .from(table)
          .where(
            and(owned(input.organizationId, input.projectId), eq(table.requestId, input.requestId)),
          );
        if (existing) {
          if (
            existing.name !== input.name ||
            !isDeepStrictEqual(existing.config, input.config) ||
            !isDeepStrictEqual(existing.restoreSource, input.restoreSource ?? null)
          )
            throw conflict("This request was already used for another database configuration.");
          return { row: existing, started: false };
        }
        const [named] = await tx
          .select({ id: table.id })
          .from(table)
          .where(
            and(
              owned(input.organizationId, input.projectId),
              eq(table.name, input.name),
              ne(table.status, "deleted"),
            ),
          );
        if (named) throw conflict("A database already uses this name in the project.");
        await lockDestinations(tx, input.organizationId, [
          input.config.backup?.destinationId,
          input.restoreSource?.destinationId,
        ]);
        const [row] = await tx
          .insert(table)
          .values({ ...input, leaseExpiresAt: new Date(Date.now() + CLUSTER_DATABASE_LEASE_MS) })
          .returning();
        return { row: row!, started: true };
      });
    },
    async change(
      org: string,
      projectId: string,
      id: string,
      expectedSequence: number,
      action: "apply" | "retry" | "remove" | "backup",
      options: { config?: ClusterDatabaseConfig; deleteData?: boolean } = {},
    ) {
      await expire(org);
      return db.transaction(async (tx) => {
        const [current] = await tx
          .select()
          .from(table)
          .where(and(owned(org, projectId), eq(table.id, id)))
          .for("update");
        if (!current) throw new NotFoundError("Database", id);
        if (current.sequence !== expectedSequence)
          throw conflict("Database setup changed. Refresh before continuing.");
        if (clusterDatabaseRunning(current.status))
          throw conflict("An operation is already running for this database.");
        if (current.status === "deleted") throw conflict("This database has been deleted.");
        if (action === "retry" && !["failed", "interrupted"].includes(current.status))
          throw conflict("Only failed or interrupted operations can be retried.");
        if (action === "apply" && current.status !== "ready")
          throw conflict("Finish or retry database setup before changing its settings.");
        if (
          action === "backup" &&
          (current.status !== "ready" ||
            !current.config.backup ||
            current.config.engine !== "postgres")
        )
          throw conflict("Configure PostgreSQL archive backups and finish database setup first.");
        if (action === "remove" && current.envKey)
          throw conflict("Disconnect this database from the application before removing it.");
        if (
          action === "remove" &&
          current.intent === "remove" &&
          current.deleteData &&
          !options.deleteData
        )
          throw conflict(
            "Permanent data deletion has already started. Retry that removal to finish cleanup.",
          );
        if (options.config) validateClusterDatabaseUpdate(current.config, options.config);
        if (options.config?.backup)
          await lockDestinations(tx, org, [options.config.backup.destinationId]);
        const intent = action === "retry" ? current.intent : action;
        const [row] = await tx
          .update(table)
          .set({
            config: options.config ?? current.config,
            intent,
            backupRequestId: action === "backup" ? crypto.randomUUID() : current.backupRequestId,
            deleteData: action === "remove" ? !!options.deleteData : current.deleteData,
            status: intent === "remove" ? "deleting" : "provisioning",
            generation: current.generation + 1,
            sequence: current.sequence + 1,
            error: null,
            observation: null,
            leaseExpiresAt: new Date(Date.now() + CLUSTER_DATABASE_LEASE_MS),
            updatedAt: new Date(),
          })
          .where(eq(table.id, id))
          .returning();
        return row!;
      });
    },
    async connect(
      org: string,
      projectId: string,
      id: string,
      expectedSequence: number,
      envKey: string | null,
      encryptedValue: string | null,
    ) {
      return db.transaction(async (tx) => {
        const [p] = await tx
          .select()
          .from(project)
          .where(and(eq(project.id, projectId), eq(project.organizationId, org)))
          .for("update");
        const [row] = await tx
          .select()
          .from(table)
          .where(and(owned(org, projectId), eq(table.id, id)))
          .for("update");
        if (!p || p.deletionInProgress || !row) throw new NotFoundError("Database", id);
        if (row.sequence !== expectedSequence || clusterDatabaseRunning(row.status))
          throw conflict("Database setup changed. Refresh before connecting it.");
        if (envKey && (row.status !== "ready" || row.clusterId !== p.clusterId))
          throw conflict("The database must be ready on the project's cluster before connecting.");
        const scope = (key: string) =>
          and(
            eq(envVar.projectId, projectId),
            eq(envVar.key, key),
            eq(envVar.environment, "production"),
            isNull(envVar.serviceId),
          );
        if (envKey) {
          const existing = await tx.select().from(envVar).where(scope(envKey)).for("update");
          if (
            existing.some((item) => row.envKey !== envKey || item.value !== row.envValueEncrypted)
          )
            throw conflict(
              `Environment variable ${envKey} already exists. Choose another name or remove the existing variable first.`,
            );
        }
        if (row.envKey && row.envValueEncrypted)
          await tx
            .delete(envVar)
            .where(and(scope(row.envKey), eq(envVar.value, row.envValueEncrypted)));
        if (envKey && encryptedValue)
          await tx
            .insert(envVar)
            .values({
              id: crypto.randomUUID(),
              projectId,
              key: envKey,
              value: encryptedValue,
              environment: "production",
              isSecret: true,
            });
        const [updated] = await tx
          .update(table)
          .set({
            envKey,
            envValueEncrypted: encryptedValue,
            sequence: row.sequence + 1,
            updatedAt: new Date(),
          })
          .where(eq(table.id, id))
          .returning();
        return updated!;
      });
    },
    async progress(id: string, generation: number, progress: ClusterDatabaseProgress) {
      const rows = await db
        .update(table)
        .set({ progress, sequence: sql`${table.sequence} + 1`, updatedAt: new Date() })
        .where(worker(id, generation))
        .returning();
      if (!rows.length) throw conflict("This worker no longer owns database setup.");
    },
    async observe(
      org: string,
      projectId: string,
      id: string,
      generation: number,
      observation: ClusterDatabaseObservation,
    ) {
      await db
        .update(table)
        .set({ observation, sequence: sql`${table.sequence} + 1` })
        .where(
          and(
            owned(org, projectId),
            eq(table.id, id),
            eq(table.generation, generation),
            inArray(table.status, ["ready", "retained"]),
          ),
        );
    },
    async active(id: string, generation: number) {
      const rows = await db.select({ id: table.id }).from(table).where(worker(id, generation));
      return rows.length === 1;
    },
    async heartbeat(id: string, generation: number) {
      const rows = await db
        .update(table)
        .set({ leaseExpiresAt: new Date(Date.now() + CLUSTER_DATABASE_LEASE_MS) })
        .where(worker(id, generation))
        .returning();
      return rows.length === 1;
    },
    async finish(
      id: string,
      generation: number,
      status: ClusterDatabaseStatus,
      observation: ClusterDatabaseObservation | null,
      error: string | null,
    ) {
      if (status === "ready" && !observation?.ready)
        throw conflict("A database cannot be ready before verification passes.");
      const rows = await db
        .update(table)
        .set({
          status,
          observation,
          error,
          sequence: sql`${table.sequence} + 1`,
          leaseExpiresAt: null,
          updatedAt: new Date(),
        })
        .where(worker(id, generation))
        .returning();
      return rows.length === 1;
    },
    interrupt: (id: string, generation: number, message: string) =>
      interrupt(and(eq(table.id, id), eq(table.generation, generation)), message),
    recoverInterrupted: (exclusive: boolean) =>
      interrupt(
        exclusive ? undefined : expired(),
        "OpenShip restarted during database setup. Retry to inspect the saved resources and continue.",
      ),
  };
}
