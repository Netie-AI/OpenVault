/**
 * Billing anniversary grant repo — idempotency claim for the
 * once-per-period quota reset performed by the anniversary cron.
 *
 * Why this exists: the cron's tick is two writes:
 *
 *   1. Oblien: `resetQuota` (zero quota_used) + `setQuota` (re-arm ceiling)
 *   2. Local: UPDATE organization.currentPeriodStart/End
 *
 * If the process crashes BETWEEN those two writes (Oblien zeroed,
 * local period not advanced), the next tick re-selects the org as a
 * candidate and re-runs step 1 — re-zeroing quota_used the user has
 * already started consuming under the new period.
 *
 * The fix is a separate claim row keyed on (organization_id,
 * period_start). The cron claims BEFORE calling Oblien; on retry the
 * unique constraint refuses the insert and the tick short-circuits.
 *
 * The row is intentionally cheap and never deleted (one row per org
 * per month — millions of orgs cost megabytes per year). Forensic
 * value: a query over this table tells operators exactly which
 * periods were granted, and a missing row identifies orgs that fell
 * through a partial outage.
 */

import { and, eq } from "drizzle-orm";
import { generateId } from "@repo/core";
import type { Database } from "../client";
import { billingAnniversaryGrant } from "../schema/billing";

export interface ClaimAnniversaryGrantInput {
  organizationId: string;
  /**
   * The START of the period being granted (NOT the end). This is the
   * point in time AFTER the cron advances the org's period column —
   * the same value that's about to be written as `currentPeriodStart`.
   */
  periodStart: Date;
}

export interface ClaimAnniversaryGrantResult {
  /**
   * True when THIS tick won the race. False when a prior tick already
   * granted for this (org, period) — the cron MUST skip the Oblien
   * resetAndRegrant call.
   */
  claimed: boolean;
}

export function createBillingAnniversaryGrantRepo(db: Database) {
  return {
    /**
     * Insert-or-no-op the per-period grant row. Returns whether THIS
     * call won the race. Unique constraint on
     * `(organization_id, period_start)` makes the operation atomic.
     */
    async claim(
      input: ClaimAnniversaryGrantInput,
    ): Promise<ClaimAnniversaryGrantResult> {
      const id = generateId("bag");
      const rows = await db
        .insert(billingAnniversaryGrant)
        .values({
          id,
          organizationId: input.organizationId,
          periodStart: input.periodStart,
        })
        .onConflictDoNothing({
          target: [
            billingAnniversaryGrant.organizationId,
            billingAnniversaryGrant.periodStart,
          ],
        })
        .returning();
      return { claimed: rows.length === 1 };
    },

    /**
     * Look up an existing claim row. Used in tests + diagnostic paths;
     * the cron itself never reads — it only claims.
     */
    async findByPeriod(
      organizationId: string,
      periodStart: Date,
    ): Promise<{ id: string; grantedAt: Date } | null> {
      const [row] = await db
        .select({
          id: billingAnniversaryGrant.id,
          grantedAt: billingAnniversaryGrant.grantedAt,
        })
        .from(billingAnniversaryGrant)
        .where(
          and(
            eq(billingAnniversaryGrant.organizationId, organizationId),
            eq(billingAnniversaryGrant.periodStart, periodStart),
          ),
        )
        .limit(1);
      return row ?? null;
    },
  };
}
