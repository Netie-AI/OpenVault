import { AppError, resolvePlan, type PlanTierId } from "@repo/core";
import type { Oblien } from "@repo/adapters";
import { getOblienClient } from "./oblien-client";

type NamespaceLimits = NonNullable<Parameters<Oblien["namespaces"]["update"]>[1]["resource_limits"]>;

/** Customer policy is declared in the catalog. Oblien resolves capacity and
 * meters credits; Openship never derives a CPU/RAM budget from service counts. */
export function cloudNamespaceLimits(tier: PlanTierId) {
  return { ...resolvePlan(tier).oblienLimits };
}

export async function initialCloudNamespaceLimits(): Promise<NamespaceLimits> {
  return cloudNamespaceLimits("free");
}

/** Submit the verified subscription's declared policy under the billing lock.
 * The provider also enforces the saved paid contract before any client sync.
 * This updates no credits, usage or balance. Null VM caps inherit Oblien's
 * capacity; stricter saved caps are sent unchanged, including on renewal. */
export async function syncCloudResourceLimits(
  namespace: string,
  tier: PlanTierId,
  desired: NamespaceLimits = cloudNamespaceLimits(tier),
): Promise<void> {
  const client = getOblienClient();
  const { data: current } = await client.namespaces.get(namespace);
  if (current.slug !== namespace) throw new AppError("Cloud namespace ownership changed", 502, "CLOUD_NAMESPACE_MISMATCH");
  // Compare declared policy, never Oblien's effective_resource_limits: account
  // capacity may change without changing the customer's purchase contract.
  const matches = (limits: NamespaceLimits | null | undefined) =>
    (Object.keys(desired) as Array<keyof NamespaceLimits>).every(key => (limits?.[key] ?? null) === desired[key]);
  if (matches(current.resource_limits)) return;
  const { data: updated } = await client.namespaces.update(current.id, { resource_limits: { ...desired } });
  if (updated.slug !== namespace || !matches(updated.resource_limits)) {
    throw new AppError("Cloud resource limits were not confirmed", 502, "CLOUD_RESOURCE_LIMITS_UNCONFIRMED");
  }
}
