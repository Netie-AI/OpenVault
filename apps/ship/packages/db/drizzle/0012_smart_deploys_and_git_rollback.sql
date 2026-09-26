-- Smart per-service deploys + git-strategy rollback
--
-- Adds the schema surface for two related features:
--
--   1. Smart per-service deploys — record which paths changed between
--      the previous successful deploy and this one (`changed_paths`),
--      let the build pipeline skip services whose root_directory was
--      untouched, and surface the result per-service in the existing
--      service_deployment table.
--
--   2. Git-strategy rollback — instead of always archiving each
--      deploy's image + workspace snapshot, projects can opt to roll
--      back by checking out the prior commit and rebuilding
--      (`rollback_strategy = "git"`). Cheaper on disk, slower to
--      restore. Snapshotted per-deployment via `commit_sha_before`.
--
-- Idempotent (IF NOT EXISTS guards) so a partial re-apply is safe.

-- ── deployment ───────────────────────────────────────────────────────────────
ALTER TABLE "deployment" ADD COLUMN IF NOT EXISTS "commit_sha_before" text;--> statement-breakpoint
ALTER TABLE "deployment" ADD COLUMN IF NOT EXISTS "changed_paths" jsonb;--> statement-breakpoint
ALTER TABLE "deployment" ADD COLUMN IF NOT EXISTS "changed_paths_truncated" boolean DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE "deployment" ADD COLUMN IF NOT EXISTS "force_all" boolean DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE "deployment" ADD COLUMN IF NOT EXISTS "rollback_strategy" text DEFAULT 'snapshot' NOT NULL;--> statement-breakpoint

-- ── project ──────────────────────────────────────────────────────────────────
ALTER TABLE "project" ADD COLUMN IF NOT EXISTS "default_rollback_strategy" text DEFAULT 'snapshot' NOT NULL;--> statement-breakpoint
ALTER TABLE "project" ADD COLUMN IF NOT EXISTS "force_deploy_next" boolean DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE "project" ADD COLUMN IF NOT EXISTS "monorepo_shared_paths" jsonb;--> statement-breakpoint
ALTER TABLE "project" ADD COLUMN IF NOT EXISTS "always_rebuild_paths" jsonb;--> statement-breakpoint

-- ── service ──────────────────────────────────────────────────────────────────
ALTER TABLE "service" ADD COLUMN IF NOT EXISTS "always_rebuild_globs" jsonb;--> statement-breakpoint

-- ── service_deployment: smart-deploy tracking columns ───────────────────────
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "service_name" text;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "reason" text;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "reason_skipped" text;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "url" text;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "started_at" timestamp;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "finished_at" timestamp;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "duration_ms" integer;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "error_message" text;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "error" text;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "check_run_id" bigint;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "check_run_url" text;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "artifact_retained_at" timestamp;--> statement-breakpoint
ALTER TABLE "service_deployment" ADD COLUMN IF NOT EXISTS "logs_ref" text;--> statement-breakpoint

-- service_deployment indexes
CREATE UNIQUE INDEX IF NOT EXISTS "uq_service_deployment_dep_svc" ON "service_deployment" USING btree ("deployment_id","service_id");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "ix_service_deployment_deployment" ON "service_deployment" USING btree ("deployment_id");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "ix_service_deployment_service_status" ON "service_deployment" USING btree ("service_id","status");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "ix_service_deployment_service_created" ON "service_deployment" USING btree ("service_id","created_at");--> statement-breakpoint

-- ── deployment_check_run ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS "deployment_check_run" (
  "id" text PRIMARY KEY NOT NULL,
  "deployment_id" text NOT NULL,
  "check_run_id" bigint NOT NULL,
  "name" text NOT NULL,
  "kind" text NOT NULL,
  "service_deployment_id" text,
  "status" text NOT NULL,
  "conclusion" text,
  "created_at" timestamp DEFAULT now() NOT NULL,
  "updated_at" timestamp DEFAULT now() NOT NULL
);--> statement-breakpoint

DO $$ BEGIN
  ALTER TABLE "deployment_check_run" ADD CONSTRAINT "deployment_check_run_deployment_id_deployment_id_fk"
    FOREIGN KEY ("deployment_id") REFERENCES "public"."deployment"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;--> statement-breakpoint

DO $$ BEGIN
  ALTER TABLE "deployment_check_run" ADD CONSTRAINT "deployment_check_run_service_deployment_id_service_deployment_id_fk"
    FOREIGN KEY ("service_deployment_id") REFERENCES "public"."service_deployment"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS "uq_deployment_check_run_per_kind" ON "deployment_check_run" USING btree ("deployment_id","kind","service_deployment_id");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "ix_deployment_check_run_deployment" ON "deployment_check_run" USING btree ("deployment_id");
