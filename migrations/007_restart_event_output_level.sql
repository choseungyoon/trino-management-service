-- 007 — allow `output` as a restart event level.
--
-- When TMS runs the restart itself (cluster_ops.restart_mode: ansible) it
-- streams the playbook's output into the sequence log, so the operator watches
-- the restart happen rather than a spinner. Those lines are verbatim text from
-- another program, not something TMS is asserting, and the UI renders them as
-- a terminal instead of as prose - so they need their own level rather than
-- being passed off as `info`.
--
-- 004 wrote the original three-value constraint. Constraints cannot be added
-- to without being replaced, so this drops and recreates it; the code-side
-- whitelist lives in tms/ops/sequence.py (ALLOWED_LEVELS) and a test compares
-- the two, reading the *last* definition across all migrations.
--
-- Run as the schema owner:
--   psql -h <db-host> -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/007_restart_event_output_level.sql

BEGIN;

ALTER TABLE restart_sequence_event
    DROP CONSTRAINT IF EXISTS restart_sequence_event_level_valid;

ALTER TABLE restart_sequence_event
    ADD CONSTRAINT restart_sequence_event_level_valid CHECK (
        level IN ('info', 'warn', 'error', 'output')
    );

COMMIT;
