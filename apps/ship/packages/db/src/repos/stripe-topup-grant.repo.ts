/**
 * Stripe topup grant repo — idempotency claim for one-shot credit-pack
 * purchases on inbound `checkout.session.completed` webhooks.
 *
 * Why this exists: `addQuota` is read-modify-write on Oblien
 * (`getDetails` current ceiling, then `setQuota` to current + delta).
 * Stripe will retry a webhook up to ~3 days if any reply was non-2xx
 * (and occasionally even after 2xx in narrow failure windows). Without
 * a single-source-of-truth claim, the second retry's `addQuota` would
 * compound on top of the first, double-crediting the org.
 *
 * The claim row's primary key for our purposes is `checkout_session_id`
 * (uniquely-indexed). Callers do `INSERT … ON CONFLICT DO NOTHING
 * RETURNING id` BEFORE the Oblien call; a missed insert means a peer
 * has already credited this session — skip.
 */

import { generateId } from "@repo/core";
import type { Database } from "../client";
import { stripeTopupGrant } from "../schema/billing";

export interface ClaimTopupGrantInput {
  checkoutSessionId: string;
  organizationId: string;
  packId: string;
  creditsMilli: number;
}

export interface ClaimTopupGrantResult {
  /**
   * True when THIS call won the race and the caller should proceed
   * with the Oblien addQuota. False when a peer (or earlier retry)
   * already wrote the row — the caller MUST skip the Oblien call.
   */
  claimed: boolean;
}

export function createStripeTopupGrantRepo(db: Database) {
  return {
    /**
     * Atomic claim. Insert-or-no-op keyed on `checkout_session_id` so
     * concurrent Stripe webhook deliveries collapse onto a single grant.
     *
     * The `.returning({id})` is what tells us whether OUR insert
     * actually landed. On Postgres, ON CONFLICT DO NOTHING returns 0
     * rows when the conflict triggered — so `rows.length === 1` means
     * the claim is ours.
     */
    async claim(input: ClaimTopupGrantInput): Promise<ClaimTopupGrantResult> {
      const id = generateId("stg");
      const rows = await db
        .insert(stripeTopupGrant)
        .values({
          id,
          checkoutSessionId: input.checkoutSessionId,
          organizationId: input.organizationId,
          packId: input.packId,
          creditsMilli: input.creditsMilli,
        })
        .onConflictDoNothing({ target: stripeTopupGrant.checkoutSessionId })
        .returning();
      return { claimed: rows.length === 1 };
    },
  };
}
