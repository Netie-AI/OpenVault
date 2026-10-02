-- ── Fix deployment_check_run uniqueness for rollup rows ─────────────────────
--
-- The previous `uq_deployment_check_run_per_kind` index covers
-- (deployment_id, kind, service_deployment_id). PostgreSQL treats NULLs
-- as distinct in unique indexes by default, so multiple rows with
-- (deployment_id, 'rollup', NULL) could coexist — the rollup invariant
-- of "one rollup row per deployment" was never actually enforced.
--
-- This migration replaces that single non-effective index with two
-- partial unique indexes that match the actual data shapes:
--
--   - rollup rows  (kind = 'rollup', service_deployment_id IS NULL):
--     unique on (deployment_id)
--
--   - service rows (service_deployment_id IS NOT NULL):
--     unique on (deployment_id, service_deployment_id)
--
-- Also drops a couple of redundant secondary indexes whose lookups are
-- already covered by the leading column of an existing unique index.

DROP INDEX IF EXISTS "uq_deployment_check_run_per_kind";--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS "uq_deployment_check_run_rollup"
  ON "deployment_check_run" ("deployment_id")
  WHERE "kind" = 'rollup';--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS "uq_deployment_check_run_service"
  ON "deployment_check_run" ("deployment_id", "service_deployment_id")
  WHERE "service_deployment_id" IS NOT NULL;--> statement-breakpoint

-- Redundant: covered by `uq_deployment_check_run_rollup` /
-- `uq_deployment_check_run_service` (both have deployment_id as the
-- leading column), so a separate ix on deployment_id buys nothing.
DROP INDEX IF EXISTS "ix_deployment_check_run_deployment";--> statement-breakpoint

-- Redundant: covered by `uq_service_deployment_dep_svc` whose leading
-- column is deployment_id.
DROP INDEX IF EXISTS "ix_service_deployment_deployment";
