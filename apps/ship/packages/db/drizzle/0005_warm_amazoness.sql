ALTER TABLE "instance_settings" ADD COLUMN "team_mode" text DEFAULT 'single_user' NOT NULL;--> statement-breakpoint
ALTER TABLE "instance_settings" ADD COLUMN "migration_target_url" text;--> statement-breakpoint
ALTER TABLE "instance_settings" ADD COLUMN "migrated_at" timestamp;