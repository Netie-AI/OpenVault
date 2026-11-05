-- Billing & credits: add per-org plan/subscription columns and the credit
-- ledger tables. Idempotent (IF NOT EXISTS guards) so applying twice on a
-- partway-through DB is a no-op.

-- ── organization: billing columns ───────────────────────────────────────────
ALTER TABLE "organization" ADD COLUMN IF NOT EXISTS "plan_tier_id" text DEFAULT 'free' NOT NULL;--> statement-breakpoint
ALTER TABLE "organization" ADD COLUMN IF NOT EXISTS "stripe_customer_id" text;--> statement-breakpoint
ALTER TABLE "organization" ADD COLUMN IF NOT EXISTS "subscription_status" text DEFAULT 'active' NOT NULL;--> statement-breakpoint
ALTER TABLE "organization" ADD COLUMN IF NOT EXISTS "current_period_start" timestamp;--> statement-breakpoint
ALTER TABLE "organization" ADD COLUMN IF NOT EXISTS "current_period_end" timestamp;--> statement-breakpoint
ALTER TABLE "organization" ADD COLUMN IF NOT EXISTS "oblien_namespace" text;--> statement-breakpoint

-- ── billing_customer ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS "billing_customer" (
  "id" text PRIMARY KEY NOT NULL,
  "organization_id" text NOT NULL,
  "stripe_customer_id" text NOT NULL,
  "email" text NOT NULL,
  "created_at" timestamp DEFAULT now() NOT NULL,
  "updated_at" timestamp DEFAULT now() NOT NULL
);--> statement-breakpoint

DO $$ BEGIN
  ALTER TABLE "billing_customer" ADD CONSTRAINT "billing_customer_organization_id_organization_id_fk"
    FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS "uq_billing_customer_org" ON "billing_customer" USING btree ("organization_id");--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "uq_billing_customer_stripe" ON "billing_customer" USING btree ("stripe_customer_id");--> statement-breakpoint

-- ── billing_subscription ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS "billing_subscription" (
  "id" text PRIMARY KEY NOT NULL,
  "organization_id" text NOT NULL,
  "stripe_subscription_id" text NOT NULL,
  "stripe_price_id" text NOT NULL,
  "plan_tier_id" text NOT NULL,
  "interval" text NOT NULL,
  "status" text NOT NULL,
  "current_period_start" timestamp NOT NULL,
  "current_period_end" timestamp NOT NULL,
  "cancel_at_period_end" boolean DEFAULT false NOT NULL,
  "created_at" timestamp DEFAULT now() NOT NULL,
  "updated_at" timestamp DEFAULT now() NOT NULL
);--> statement-breakpoint

DO $$ BEGIN
  ALTER TABLE "billing_subscription" ADD CONSTRAINT "billing_subscription_organization_id_organization_id_fk"
    FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS "uq_billing_subscription_stripe" ON "billing_subscription" USING btree ("stripe_subscription_id");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_billing_subscription_org" ON "billing_subscription" USING btree ("organization_id");--> statement-breakpoint

-- ── credit_balance ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS "credit_balance" (
  "organization_id" text PRIMARY KEY NOT NULL,
  "monthly_free_milli" bigint DEFAULT 0 NOT NULL,
  "topup_milli" bigint DEFAULT 0 NOT NULL,
  "updated_at" timestamp DEFAULT now() NOT NULL
);--> statement-breakpoint

DO $$ BEGIN
  ALTER TABLE "credit_balance" ADD CONSTRAINT "credit_balance_organization_id_organization_id_fk"
    FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;--> statement-breakpoint

-- ── credit_grant ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS "credit_grant" (
  "id" text PRIMARY KEY NOT NULL,
  "organization_id" text NOT NULL,
  "amount_milli" bigint NOT NULL,
  "bucket" text NOT NULL,
  "source" text NOT NULL,
  "source_ref" text,
  "expires_at" timestamp,
  "created_at" timestamp DEFAULT now() NOT NULL
);--> statement-breakpoint

DO $$ BEGIN
  ALTER TABLE "credit_grant" ADD CONSTRAINT "credit_grant_organization_id_organization_id_fk"
    FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;--> statement-breakpoint

CREATE INDEX IF NOT EXISTS "idx_credit_grant_org_created" ON "credit_grant" USING btree ("organization_id","created_at");--> statement-breakpoint

-- ── credit_consumption ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS "credit_consumption" (
  "id" text PRIMARY KEY NOT NULL,
  "organization_id" text NOT NULL,
  "oblien_event_id" text NOT NULL,
  "resource_type" text NOT NULL,
  "quantity_milli" bigint NOT NULL,
  "credits_milli" bigint NOT NULL,
  "bucket_consumed" text NOT NULL,
  "occurred_at" timestamp NOT NULL,
  "recorded_at" timestamp DEFAULT now() NOT NULL
);--> statement-breakpoint

DO $$ BEGIN
  ALTER TABLE "credit_consumption" ADD CONSTRAINT "credit_consumption_organization_id_organization_id_fk"
    FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS "uq_credit_consumption_org_event" ON "credit_consumption" USING btree ("organization_id","oblien_event_id");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_credit_consumption_org_occurred" ON "credit_consumption" USING btree ("organization_id","occurred_at");--> statement-breakpoint

-- ── credit_pack ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS "credit_pack" (
  "id" text PRIMARY KEY NOT NULL,
  "name" text NOT NULL,
  "credits_milli" bigint NOT NULL,
  "price_cents" integer NOT NULL,
  "stripe_product_id" text NOT NULL,
  "stripe_price_id" text NOT NULL,
  "active" boolean DEFAULT true NOT NULL,
  "sort_order" integer DEFAULT 0 NOT NULL
);--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS "uq_credit_pack_stripe_price" ON "credit_pack" USING btree ("stripe_price_id");--> statement-breakpoint

-- ── oblien_usage_cursor ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS "oblien_usage_cursor" (
  "organization_id" text PRIMARY KEY NOT NULL,
  "oblien_namespace" text NOT NULL,
  "last_synced_at" timestamp NOT NULL,
  "last_sync_run_at" timestamp NOT NULL,
  "consecutive_failures" integer DEFAULT 0 NOT NULL
);--> statement-breakpoint

DO $$ BEGIN
  ALTER TABLE "oblien_usage_cursor" ADD CONSTRAINT "oblien_usage_cursor_organization_id_organization_id_fk"
    FOREIGN KEY ("organization_id") REFERENCES "public"."organization"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;--> statement-breakpoint

-- ── stripe_webhook_event ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS "stripe_webhook_event" (
  "stripe_event_id" text PRIMARY KEY NOT NULL,
  "event_type" text NOT NULL,
  "received_at" timestamp DEFAULT now() NOT NULL,
  "processed_at" timestamp
);
