-- Versions now represent shipped releases: a deployment gets its version number
-- in onSuccess (per-commit), not at create. Clear the per-attempt numbers that
-- the old MAX(version)+1-at-create logic assigned to non-successful deploys so
-- failed/in-flight rows stop showing a stale vN badge. Successful (ready) rows
-- keep their existing numbers.
UPDATE "deployment" SET "version" = NULL WHERE "status" <> 'ready';
