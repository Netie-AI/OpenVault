-- Flip the default rollback strategy for NEW projects from "snapshot" to
-- "git". Existing rows are not touched — they keep whatever they were
-- created with (a previously-deployed project relying on snapshot
-- rollback should not silently lose its snapshots).
--
-- Users can change the per-project setting via the dashboard's git
-- settings page.

ALTER TABLE "project" ALTER COLUMN "default_rollback_strategy" SET DEFAULT 'git';
