-- Privileges for 016. GRANTs in 002 are per-table, so each new table extends
-- them (same pattern as 005, 011, 013 and 015).
--
-- Run as the database owner AFTER 016.

\set app_role 'tms_app'

BEGIN;

-- The run row moves through states, so it needs UPDATE.
GRANT SELECT, INSERT, UPDATE ON benchmark_run TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE benchmark_run_id_seq TO :"app_role";

-- Measurements are append-only: INSERT and SELECT, never UPDATE, never DELETE.
-- A number that can be edited after the fact is not a measurement, and the
-- whole point of storing these is to compare a run against one from before
-- somebody changed something.
GRANT SELECT, INSERT ON benchmark_result TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE benchmark_result_id_seq TO :"app_role";

COMMIT;
