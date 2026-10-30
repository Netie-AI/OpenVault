-- Demolish the local credit ledger.
--
-- Quota is now owned by Oblien (the runtime meters consumption directly and
-- enforces `setQuota` limits). The local grant/consume/balance triplet and
-- the Oblien pull-sync cursor are obsolete. The Stripe-side state lives on
-- in billing_customer / billing_subscription / credit_pack / stripe_webhook_event.
--
-- Order matters: drop indexes first (defensive — DROP TABLE drops them too,
-- but being explicit keeps the diff easy to audit), then the tables in
-- dependency order. There are no FKs between the four dropped tables, but
-- credit_consumption + credit_grant + credit_balance + oblien_usage_cursor
-- all reference organization.id with ON DELETE CASCADE, which postgres
-- handles cleanly regardless of drop order.

DROP INDEX IF EXISTS "uq_credit_grant_org_source_ref";--> statement-breakpoint
DROP INDEX IF EXISTS "idx_credit_grant_org_created";--> statement-breakpoint
DROP INDEX IF EXISTS "uq_credit_consumption_org_event";--> statement-breakpoint
DROP INDEX IF EXISTS "idx_credit_consumption_org_occurred";--> statement-breakpoint

DROP TABLE IF EXISTS "credit_consumption";--> statement-breakpoint
DROP TABLE IF EXISTS "credit_balance";--> statement-breakpoint
DROP TABLE IF EXISTS "credit_grant";--> statement-breakpoint
DROP TABLE IF EXISTS "oblien_usage_cursor";
