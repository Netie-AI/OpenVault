import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ quota: vi.fn(), get: vi.fn(), update: vi.fn() }));
vi.mock("@repo/platform/engine/lib/oblien-client", () => ({
  getOblienClient: () => ({ workspaces: { getQuota: h.quota }, namespaces: { get: h.get, update: h.update } }),
}));
import { cloudNamespaceLimits, initialCloudNamespaceLimits, syncCloudResourceLimits } from "@repo/platform/engine/lib/cloud-resource-limits";

beforeEach(() => {
  vi.resetAllMocks();
  h.quota.mockImplementation(() => { throw new Error("Openship must not calculate provider capacity"); });
  h.get.mockResolvedValue({ data: { id: "ns-a", slug: "tenant-a", resource_limits: null } });
  h.update.mockImplementation(async (_id, input) => ({ data: { id: "ns-a", slug: "tenant-a", ...input,
    effective_resource_limits: { max_workspaces: 52, max_vcpus: 32, max_ram_mb: 65536, max_disk_gb: 1024 },
  } }));
});

describe("Oblien-owned namespace capacity", () => {
  it("inherits machine capacity without inventing a service-count CPU or RAM budget", async () => {
    expect(cloudNamespaceLimits("team")).toEqual({ max_workspaces: 52, max_vcpus: null, max_ram_mb: null, max_disk_gb: null });
    await expect(initialCloudNamespaceLimits()).resolves.toEqual({ max_workspaces: 2, max_vcpus: null, max_ram_mb: null, max_disk_gb: null });
    await syncCloudResourceLimits("tenant-a", "team");
    expect(h.update).toHaveBeenCalledWith("ns-a", { resource_limits: cloudNamespaceLimits("team") });
    expect(h.quota).not.toHaveBeenCalled();
  });
  it("sends an older saved offer unchanged and accepts Oblien's lower effective ceiling", async () => {
    const saved = Object.freeze({ max_workspaces: 52, max_vcpus: 200, max_ram_mb: 417792, max_disk_gb: 64 });
    await syncCloudResourceLimits("tenant-a", "team", saved);
    expect(h.update).toHaveBeenCalledWith("ns-a", { resource_limits: saved });
    expect(saved.max_vcpus).toBe(200);
    expect(h.quota).not.toHaveBeenCalled();
  });
  it("preserves explicit customer restrictions even when the owner's capacity is unlimited", async () => {
    const saved = Object.freeze({ max_workspaces: 7, max_vcpus: 2, max_ram_mb: 4096, max_disk_gb: 12 });
    await syncCloudResourceLimits("tenant-a", "team", saved);
    expect(h.update).toHaveBeenCalledWith("ns-a", { resource_limits: saved });
    expect(h.quota).not.toHaveBeenCalled();
  });
  it("does not rewrite declared policy when effective capacity differs", async () => {
    const declared = { max_workspaces: 52, max_vcpus: 200, max_ram_mb: 417792, max_disk_gb: 64 };
    h.get.mockResolvedValue({ data: { id: "ns-a", slug: "tenant-a", resource_limits: declared,
      effective_resource_limits: { ...declared, max_vcpus: 32, max_ram_mb: 65536 },
    } });
    await syncCloudResourceLimits("tenant-a", "team", declared);
    expect(h.update).not.toHaveBeenCalled();
    expect(h.quota).not.toHaveBeenCalled();
  });
  it("clears old declared caps only after a verified enterprise entitlement", async () => {
    h.get.mockResolvedValue({ data: { id: "ns-a", slug: "tenant-a", resource_limits: { max_workspaces: 5 } } });
    await syncCloudResourceLimits("tenant-a", "enterprise");
    expect(h.update).toHaveBeenCalledWith("ns-a", { resource_limits: { max_workspaces: null, max_vcpus: null, max_ram_mb: null, max_disk_gb: null } });
  });
  it("cannot change policy when the provider returns another namespace", async () => {
    h.get.mockResolvedValue({ data: { id: "ns-b", slug: "tenant-b" } });
    await expect(syncCloudResourceLimits("tenant-a", "pro")).rejects.toMatchObject({ code: "CLOUD_NAMESPACE_MISMATCH" });
    expect(h.update).not.toHaveBeenCalled();
  });
  it.each([
    { id: "ns-a", slug: "tenant-a", resource_limits: null },
    { id: "ns-b", slug: "tenant-b", resource_limits: cloudNamespaceLimits("pro") },
  ])("refuses deployment if the declared policy update is not confirmed", async data => {
    h.update.mockResolvedValue({ data });
    await expect(syncCloudResourceLimits("tenant-a", "pro")).rejects.toMatchObject({ code: "CLOUD_RESOURCE_LIMITS_UNCONFIRMED" });
  });
});
