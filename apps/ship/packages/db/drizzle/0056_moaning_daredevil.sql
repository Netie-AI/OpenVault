ALTER TABLE "cloud_handoff_code" ADD COLUMN "state" text;--> statement-breakpoint
CREATE INDEX "cloud_handoff_code_state_idx" ON "cloud_handoff_code" USING btree ("state");