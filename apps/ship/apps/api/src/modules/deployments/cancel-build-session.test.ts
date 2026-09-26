import { beforeEach, describe, expect, it, vi } from "vitest";
import { cancelBuildSession } from "@repo/platform/engine/modules/deployments/build.service";

const h = vi.hoisted(() => ({
  session: { id: "session-1", startedAt: null as Date | null },
  cancelInFlight: vi.fn(),
  acknowledgeUnstarted: vi.fn(),
  cancelWorker: vi.fn(),
  quiescent: vi.fn(),
  collect: vi.fn(),
  cleanup: vi.fn(),
  updateStatus: vi.fn(),
}));

vi.mock("@repo/db", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  repos: {
    deployment: {
      findById: async () => ({
        id: "dep-1",
        projectId: "project-1",
        organizationId: "org-1",
        status: "queued",
      }),
      // Return a snapshot, just as a database read does, rather than a shared
      // object the mocked racing kickoff could retroactively change.
      findBuildSessionByDeploymentId: async () => ({ ...h.session }),
      cancelInFlight: h.cancelInFlight,
      acknowledgeUnstartedBuildSession: h.acknowledgeUnstarted,
    },
    project: { findById: async () => ({ id: "project-1", organizationId: "org-1" }) },
    service: { listByProject: async () => [] },
  },
}));
vi.mock("@repo/platform/engine/lib/platform-config", () => ({ platform: () => ({ runtime: {} }) }));
vi.mock("@repo/platform/engine/modules/deployments/build-pipeline", () => ({
  kickoffBuild: vi.fn(),
  resolveServicePipelineMode: vi.fn(),
}));
vi.mock("@repo/platform/engine/modules/deployments/deployment-cancellation", () => ({
  requestDeploymentCancellation: h.cancelWorker,
  waitForDeploymentQuiescence: h.quiescent,
}));
vi.mock("@repo/platform/engine/modules/deployments/session-manager", () => ({
  cancelPendingPrompt: vi.fn(),
  updateStatus: h.updateStatus,
}));
vi.mock("@repo/platform/engine/modules/projects/project-cleanup.service", () => ({
  collectDeploymentManifest: h.collect,
  executeCleanup: h.cleanup,
}));

beforeEach(() => {
  vi.clearAllMocks();
  h.session.startedAt = null;
  h.cancelInFlight.mockResolvedValue(true);
  h.quiescent.mockResolvedValue(false);
  h.collect.mockResolvedValue({ projectId: "project-1", resources: [{ id: "container-1" }] });
  h.cleanup.mockResolvedValue(undefined);
});

describe("cancel request and worker ownership", () => {
  it("recognizes a worker that started while the cancellation request was waiting", async () => {
    h.cancelInFlight.mockImplementation(async () => {
      h.session.startedAt = new Date();
      return true;
    });
    expect(await cancelBuildSession("dep-1")).toMatchObject({ pending: true, success: false });
    expect(h.cancelWorker).toHaveBeenCalledWith("dep-1", { keepProvisioned: undefined });
    expect(h.collect).not.toHaveBeenCalled();
    expect(h.cleanup).not.toHaveBeenCalled();
    expect(h.updateStatus).toHaveBeenCalledWith("dep-1", "cancelled");
  });

  it("owns cleanup and completion when no worker ever started", async () => {
    h.quiescent.mockResolvedValue(true);
    expect(await cancelBuildSession("dep-1")).toMatchObject({ pending: false, success: true });
    expect(h.cleanup).toHaveBeenCalledTimes(1);
    expect(h.acknowledgeUnstarted).toHaveBeenCalledWith("session-1");
  });

  it("does not cancel or clean up when the deployment completed first", async () => {
    h.cancelInFlight.mockResolvedValue(false);
    await expect(cancelBuildSession("dep-1")).rejects.toThrow("no longer in progress");
    expect(h.cancelWorker).not.toHaveBeenCalled();
    expect(h.collect).not.toHaveBeenCalled();
    expect(h.acknowledgeUnstarted).not.toHaveBeenCalled();
  });
});
