ALTER TABLE "project" ADD COLUMN IF NOT EXISTS "cloud_workspace_id" text;--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "uq_project_cloud_workspace_id" ON "project" USING btree ("cloud_workspace_id") WHERE "project"."cloud_workspace_id" IS NOT NULL AND "project"."deleted_at" IS NULL;
