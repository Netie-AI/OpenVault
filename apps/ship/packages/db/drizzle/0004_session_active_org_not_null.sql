-- Backfill any remaining nulls (from sessions created before the
-- create-time hook landed) to the user's personal org. The
-- provisionUser convention is org_<userId>, and provisionUser
-- guarantees that org + owner-member row exist for every user.
UPDATE "session" SET "active_organization_id" = 'org_' || "user_id"
  WHERE "active_organization_id" IS NULL;
--> statement-breakpoint
-- Promote to NOT NULL — the schema itself now enforces the
-- invariant. No code path can violate it.
ALTER TABLE "session" ALTER COLUMN "active_organization_id" SET NOT NULL;
