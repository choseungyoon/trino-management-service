-- Privileges for 012. GRANTs in 002 are per-table, so each new table extends
-- them (same pattern as 005 and 011).
--
-- Run as the database owner AFTER 012.

\set app_role 'tms_app'

BEGIN;

-- The run row moves through states, so it needs UPDATE.
GRANT SELECT, INSERT, UPDATE ON fleet_job_run TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE fleet_job_run_id_seq TO :"app_role";

-- The output is append-only, like audit_action and restart_sequence_event:
-- INSERT and SELECT, never UPDATE, never DELETE. It is what an operator will
-- read back to work out what a playbook actually did.
GRANT SELECT, INSERT ON fleet_job_output TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE fleet_job_output_id_seq TO :"app_role";

COMMIT;
