-- HIGH-finding fixes for GitHub auth surface (audit, June 2026).
--
-- HIGH #6 — github_install_state: short-TTL nonces that bind the install
-- URL the SaaS hands back to a specific local user/org. Resolves the
-- "echo-back state with no local binding" hole in the install flow.
CREATE TABLE "github_install_state" (
	"id" text PRIMARY KEY NOT NULL,
	"state" text NOT NULL,
	"user_id" text NOT NULL,
	"organization_id" text,
	"expires_at" timestamp NOT NULL,
	"created_at" timestamp DEFAULT now() NOT NULL,
	CONSTRAINT "github_install_state_state_unique" UNIQUE("state")
);
--> statement-breakpoint
CREATE INDEX "github_install_state_expires_idx" ON "github_install_state" USING btree ("expires_at");--> statement-breakpoint
CREATE INDEX "github_install_state_user_idx" ON "github_install_state" USING btree ("user_id");--> statement-breakpoint

-- HIGH #9 — per-project webhook signing secret (encrypted via lib/encryption).
-- Null on legacy rows; verifier falls back to env.GITHUB_WEBHOOK_SECRET there.
ALTER TABLE "project" ADD COLUMN "webhook_secret" text;--> statement-breakpoint

-- HIGH #7 — explicit operator opt-in for the gh CLI escape hatch.
-- Without this, "owner of any org" gated transitive access to the
-- operator's broad-scope PAT.
ALTER TABLE "user_settings" ADD COLUMN "gh_cli_operator_opted_in" boolean DEFAULT false NOT NULL;--> statement-breakpoint

-- HIGH #10 — PAT scope cached from inspectPatScope so we can re-validate
-- at use time without re-issuing `GET /user`.
ALTER TABLE "user_settings" ADD COLUMN "pat_scope" jsonb;
