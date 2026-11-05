ALTER TABLE "instance_settings" ADD COLUMN "migration_in_progress" boolean NOT NULL DEFAULT false;
--> statement-breakpoint
ALTER TABLE "instance_settings" ADD COLUMN "migration_started_at" timestamp;
