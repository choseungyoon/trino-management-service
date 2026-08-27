-- Privileges for 020. GRANTs in 002 are per-table, so each new table extends
-- them (same pattern as 005, 011, 013, 015, 017 and 019).
--
-- Run as the database owner AFTER 020.

\set app_role 'tms_app'

BEGIN;

-- DELETE, like the query sets it points at. A schedule is configuration: it
-- says when to measure, not what was measured. Removing one takes nothing
-- away from the evidence - `benchmark_run.schedule_id` is ON DELETE SET NULL
-- precisely so the measurements outlive the reason they were collected.
GRANT SELECT, INSERT, UPDATE, DELETE ON benchmark_schedule TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE benchmark_schedule_id_seq TO :"app_role";

COMMIT;
