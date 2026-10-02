-- Partial unique index on credit_grant (organization_id, source, source_ref)
-- where source_ref IS NOT NULL. Closes the race between the Stripe webhook
-- and the monthly-reset cron: both used to mint a tier_grant for the same
-- period boundary, producing two ledger rows + a doubled allowance in the
-- cache. The unique index makes the second insert a conflict; the writers
-- use ON CONFLICT DO NOTHING and skip the cache delta when no row landed.
--
-- Partial (source_ref IS NOT NULL) so admin/refund grants minted with a
-- null source_ref are not coerced into uniqueness — those are intentionally
-- distinct ledger rows.

CREATE UNIQUE INDEX IF NOT EXISTS "uq_credit_grant_org_source_ref"
  ON "credit_grant" USING btree ("organization_id", "source", "source_ref")
  WHERE "source_ref" IS NOT NULL;
