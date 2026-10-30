import type { BillingSubscription } from "@repo/contracts";
import type { OblienEntitlement, OblienSubscription } from "../../lib/oblien-billing-api";
import { subscriptionPlan } from "./billing-catalog";

/** Extra credits are useful only while a customer's paid plan permits Cloud work. */
export function canTopUpCloudSubscription(
  subscription: OblienSubscription,
  entitlement: OblienEntitlement,
): boolean {
  return (
    subscription !== null &&
    subscriptionPlan(subscription).tier !== "free" &&
    ["active", "trialing"].includes(subscription.status) &&
    // The management record may remain active after its paid period expires.
    // Oblien's entitlement decides whether more credits can restore Cloud work.
    ["active", "credit_exhausted"].includes(entitlement.status)
  );
}

/** Keep provider identifiers out of the public application contract. */
export function presentCloudSubscription(subscription: OblienSubscription): BillingSubscription | null {
  if (!subscription) return null;
  return {
    tier: subscriptionPlan(subscription).tier,
    status: subscription.status,
    interval: subscription.billingInterval === "yearly" ? "annual" : "monthly",
    currentPeriod: { start: subscription.periodStart, end: subscription.periodEnd },
    cancelAtPeriodEnd: subscription.cancelAtPeriodEnd,
    canceledAt: subscription.canceledAt,
  };
}
