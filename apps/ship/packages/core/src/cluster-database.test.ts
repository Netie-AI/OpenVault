import { describe, expect, it } from "vitest";
import {
  clusterDatabasePodCount,
  validateClusterDatabase,
  validateClusterDatabaseUpdate,
  type ClusterDatabaseConfig,
} from "./cluster-database";
const config: ClusterDatabaseConfig = {
  engine: "postgres",
  mode: "cluster",
  instances: 3,
  storageGiB: 20,
  storageClass: "openship-local",
  cpuMillis: 500,
  memoryMiB: 512,
  databaseName: "app",
};
describe("database lifecycle capabilities", () => {
  it("distinguishes database instances from Redis shards and their replicas", () => {
    expect(clusterDatabasePodCount(config)).toBe(3);
    expect(clusterDatabasePodCount({ ...config, engine: "redis" })).toBe(6);
    expect(
      clusterDatabasePodCount({ ...config, engine: "redis", mode: "standalone", instances: 1 }),
    ).toBe(1);
  });
  it.each([
    { instances: 2 },
    { mode: "standalone", instances: 3 },
    { storageClass: "../outside" },
    { databaseName: "template0" },
    { cpuMillis: 0 },
    { memoryMiB: 128 },
  ])("refuses an unsupported database configuration %j", (patch) => {
    expect(() =>
      validateClusterDatabase({ ...config, ...patch } as ClusterDatabaseConfig),
    ).toThrow();
  });
  it("permits PostgreSQL replica changes without converting standalone data", () => {
    expect(() => validateClusterDatabaseUpdate(config, { ...config, instances: 4 })).not.toThrow();
    expect(() =>
      validateClusterDatabaseUpdate({ ...config, mode: "standalone", instances: 1 }, config),
    ).toThrow("cannot be changed");
  });
  it("refuses shrinking, local volume expansion and accidental Redis resharding", () => {
    expect(() => validateClusterDatabaseUpdate(config, { ...config, storageGiB: 19 })).toThrow(
      "shrunk",
    );
    expect(() => validateClusterDatabaseUpdate(config, { ...config, storageGiB: 30 })).toThrow(
      "cannot be resized",
    );
    expect(() =>
      validateClusterDatabaseUpdate(
        { ...config, engine: "redis" },
        { ...config, engine: "redis", instances: 4 },
      ),
    ).toThrow("migration");
  });
  it("permits CSI expansion for PostgreSQL but refuses unverified Redis volume changes", () => {
    const postgres = { ...config, storageClass: "expandable-csi" };
    expect(() =>
      validateClusterDatabaseUpdate(postgres, { ...postgres, storageGiB: 30 }),
    ).not.toThrow();
    const redis = { ...postgres, engine: "redis" as const };
    expect(() => validateClusterDatabaseUpdate(redis, { ...redis, storageGiB: 30 })).toThrow(
      "Redis volume resizing is not supported",
    );
    expect(() => validateClusterDatabaseUpdate(redis, { ...redis, memoryMiB: 1024 })).not.toThrow();
  });
  it("preserves the archive destination while allowing schedule and retention changes", () => {
    const before = {
      ...config,
      backup: { destinationId: "archives", schedule: "daily" as const, retentionDays: 30 },
    };
    expect(() =>
      validateClusterDatabaseUpdate(before, {
        ...before,
        backup: { ...before.backup, schedule: "manual", retentionDays: 60 },
      }),
    ).not.toThrow();
    expect(() => validateClusterDatabaseUpdate(before, config)).toThrow("recovery history");
    expect(() =>
      validateClusterDatabaseUpdate(before, {
        ...before,
        backup: { ...before.backup, destinationId: "another" },
      }),
    ).toThrow("recovery history");
  });
  it("rejects unsupported archive engines and retention periods", () => {
    const backup = { destinationId: "archives", schedule: "daily" as const, retentionDays: 30 };
    expect(() => validateClusterDatabase({ ...config, engine: "redis", backup })).toThrow(
      "PostgreSQL template",
    );
    expect(() =>
      validateClusterDatabase({ ...config, backup: { ...backup, retentionDays: 0 } }),
    ).toThrow("between 7 and 365");
  });
});
