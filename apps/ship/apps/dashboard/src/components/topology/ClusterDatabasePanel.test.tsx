// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ClusterDatabase } from "@repo/contracts";
import { ClusterDatabasePanel } from "./ClusterDatabasePanel";

const h = vi.hoisted(() => ({
  create: vi.fn(),
  remove: vi.fn(),
  inspect: vi.fn(),
  connect: vi.fn(),
  update: vi.fn(),
  retry: vi.fn(),
  backup: vi.fn(),
  saved: vi.fn(),
  deploy: vi.fn(),
}));
vi.mock("@/lib/api/cluster-databases", () => ({ clusterDatabasesApi: h }));
vi.mock("@/lib/api/backups", () => ({
  backupDestinationsApi: { list: vi.fn(async () => ({ data: [] })) },
}));
const row = (patch: Partial<ClusterDatabase> = {}): ClusterDatabase => ({
  id: "db",
  projectId: "project",
  clusterId: "cluster",
  name: "postgres",
  status: "ready",
  intent: "apply",
  generation: 1,
  sequence: 4,
  config: {
    engine: "postgres",
    mode: "standalone",
    instances: 1,
    storageClass: "openship-local",
    storageGiB: 20,
    cpuMillis: 500,
    memoryMiB: 512,
    databaseName: "app",
  },
  progress: { steps: [], logs: [] },
  observation: {
    ready: true,
    observedAt: new Date().toISOString(),
    primary: "database-1",
    message: "Ready",
    pods: [],
    volumes: [],
  },
  internalHost: "database.private",
  readOnlyHost: null,
  error: null,
  envKey: null,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  ...patch,
});
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  h.create.mockResolvedValue(row({ status: "provisioning" }));
  h.remove.mockResolvedValue(row({ status: "deleting", intent: "remove" }));
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
const button = (text: string) =>
  [...host.querySelectorAll("button")].find((button) => button.textContent?.trim() === text)!;
const click = (target: HTMLElement) => act(async () => target.click());
async function render(database?: ClusterDatabase) {
  await act(async () =>
    root.render(
      <ClusterDatabasePanel
        projectId="project"
        database={database}
        onSaved={h.saved}
        onDeploy={h.deploy}
        onClose={() => {}}
        onMinimize={() => {}}
      />,
    ),
  );
}
async function input(element: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("project database controls", () => {
  it("keeps the catalog simple and locks a create until its saved record returns", async () => {
    let complete!: (row: ClusterDatabase) => void;
    h.create.mockReturnValue(
      new Promise((resolve) => {
        complete = resolve;
      }),
    );
    await render();
    expect(host.querySelector("input")).toBeNull();
    const postgres = [...host.querySelectorAll("button")].find((button) =>
      button.textContent?.includes("PostgreSQL"),
    )!;
    await click(postgres);
    expect(host.textContent).toContain("Storage per instance");
    await act(async () => {
      button("Create database").click();
      host
        .querySelector("form")!
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(h.create).toHaveBeenCalledOnce();
    expect(button("Create database").disabled).toBe(true);
    await act(async () => complete(row({ status: "provisioning" })));
    expect(h.saved).toHaveBeenCalledWith(
      expect.objectContaining({ id: "db", status: "provisioning" }),
    );
  });
  it("requires cluster-aware Redis clients and explains the six-server layout", async () => {
    await render();
    await click(
      [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("Redis"))!,
    );
    await click(
      [...host.querySelectorAll("button")].find((button) =>
        button.textContent?.includes("Standalone"),
      )!,
    );
    await act(async () =>
      [...document.querySelectorAll<HTMLButtonElement>('[role="option"]')]
        .find((option) => option.textContent?.startsWith("Cluster"))!
        .click(),
    );
    expect(host.textContent).toContain("6 separate servers required");
    expect(button("Create database").disabled).toBe(true);
    await click(host.querySelector<HTMLElement>('[role="checkbox"]')!);
    expect(host.querySelector('[role="checkbox"]')?.getAttribute("aria-checked")).toBe("true");
    expect(button("Create database").disabled).toBe(false);
    // happy-dom's decimal step validation disagrees with browsers for 0.5 CPU.
    // Exercise submission here; Chromium checks the native form in the UI run.
    await act(async () =>
      host
        .querySelector("form")!
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
    );
    expect(h.create).toHaveBeenCalledWith(
      "project",
      expect.objectContaining({
        clusterAwareClient: true,
        config: expect.objectContaining({ engine: "redis", mode: "cluster", instances: 3 }),
      }),
    );
  });
  it("requires the database name and defaults to keeping its data", async () => {
    const database = row();
    await render(database);
    await click(button("Remove database"));
    expect(button("Stop and keep data").disabled).toBe(true);
    await input(
      [...host.querySelectorAll("label")]
        .find((label) => label.textContent?.includes("Type postgres to confirm"))!
        .querySelector("input")!,
      "postgres",
    );
    await click(button("Stop and keep data"));
    expect(h.remove).toHaveBeenCalledWith("project", database, "postgres", false);
  });
  it("shows only removal steps during a saved deletion", async () => {
    await render(row({ status: "deleting", intent: "remove" }));
    expect(host.textContent).toContain("Removing database");
    expect(host.textContent).toContain("Database removal");
    expect(host.textContent).not.toContain("Preparing persistent storage");
    expect(host.textContent).not.toContain("Connection and health checks");
  });
  it("restores a selected backup into a new database without changing the application's connection", async () => {
    const database = row();
    database.config.backup = { destinationId: "s3", schedule: "daily", retentionDays: 30 };
    database.envKey = "DATABASE_URL";
    database.observation!.backups = [
      {
        name: "backup-one",
        phase: "completed",
        startedAt: "2026-09-21T10:00:00Z",
        completedAt: "2026-09-21T10:01:00Z",
        error: null,
        backupId: "20260921T100000",
      },
    ];
    await render(database);
    await click(host.querySelector<HTMLElement>('[aria-label="Restore backup backup-one"]')!);
    await click(button("Restore database"));
    expect(h.create).toHaveBeenCalledWith(
      "project",
      expect.objectContaining({
        name: "postgres-restored",
        restoreFrom: { databaseId: "db", backupName: "backup-one" },
      }),
    );
    expect(h.connect).not.toHaveBeenCalled();
    expect(h.deploy).not.toHaveBeenCalled();
  });
});
