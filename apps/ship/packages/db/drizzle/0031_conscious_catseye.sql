CREATE TABLE "personal_access_token_grant" (
	"id" text PRIMARY KEY NOT NULL,
	"token_id" text NOT NULL,
	"resource_type" text NOT NULL,
	"resource_id" text NOT NULL,
	"permissions_json" text DEFAULT '[]' NOT NULL,
	"created_at" timestamp DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "personal_access_token" ADD COLUMN "scoped" boolean DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE "personal_access_token_grant" ADD CONSTRAINT "personal_access_token_grant_token_id_personal_access_token_id_fk" FOREIGN KEY ("token_id") REFERENCES "public"."personal_access_token"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "pat_grant_unique" ON "personal_access_token_grant" USING btree ("token_id","resource_type","resource_id");--> statement-breakpoint
CREATE INDEX "pat_grant_token_idx" ON "personal_access_token_grant" USING btree ("token_id");