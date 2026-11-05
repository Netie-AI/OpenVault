-- Billing idempotency: per-checkout-session topup grants + per-period
-- anniversary cron grants. Both protect read-modify-write Oblien quota
-- mutations from being re-applied when their upstream driver retries:
--   - stripe_topup_grant      Stripe webhook redelivery of checkout.session.completed
--                              would otherwise call addQuota twice → double-credit.
--   - billing_anniversary_grant  Cron crash between Oblien resetQuota and the
--                              local period UPDATE would let the next tick
--                              re-zero quota_used on the new period.
-- Idempotent (IF NOT EXISTS guards) so applying twice on a partway-through
-- DB is a no-op.

CREATE TABLE IF NOT EXISTS "stripe_topup_grant" (
  "id" text PRIMARY KEY NOT NULL,
  "checkout_session_id" text NOT NULL,
  "organization_id" text NOT NULL,
  "pack_id" text NOT NULL,
  "credits_milli" bigint NOT NULL,
  "granted_at" timestamp DEFAULT now() NOT NULL
);--> statement-breakpoint

DO $$ BEGIN
  ALTER TABLE "stripe_topup_grant" ADD CONSTRAINT "stripe_topup_grant_organization_id_organization_id_fk"
    FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS "uq_stripe_topup_grant_session" ON "stripe_topup_grant" USING btree ("checkout_session_id");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_stripe_topup_grant_org" ON "stripe_topup_grant" USING btree ("organization_id");--> statement-breakpoint

CREATE TABLE IF NOT EXISTS "billing_anniversary_grant" (
  "id" text PRIMARY KEY NOT NULL,
  "organization_id" text NOT NULL,
  "period_start" timestamp NOT NULL,
  "granted_at" timestamp DEFAULT now() NOT NULL
);--> statement-breakpoint

DO $$ BEGIN
  ALTER TABLE "billing_anniversary_grant" ADD CONSTRAINT "billing_anniversary_grant_organization_id_organization_id_fk"
    FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS "uq_billing_anniversary_grant_org_period" ON "billing_anniversary_grant" USING btree ("organization_id","period_start");
