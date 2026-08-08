-- 005 — grants for the restart sequence tables.
--
-- 002 grants per table on purpose (a table-by-table grant survives someone
-- re-running it; a blanket GRANT ALL does not), so new tables need their own.
--
-- The split matters:
--
--   restart_sequence        SELECT, INSERT, UPDATE — it is live state and the
--                           sequence advances through it.
--   restart_sequence_event  SELECT, INSERT only — no UPDATE, no DELETE. It is
--                           the record of what was done to production, and the
--                           same append-only rule as `audit_action` applies: a
--                           progress log that can be rewritten is not evidence.
--
-- If the application role is not `tms_app`, change the line below.
--
-- Run as the schema owner:
--   psql -h <db-host> -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/005_restart_sequence_grants.sql

\set app_role 'tms_app'

BEGIN;

GRANT SELECT, INSERT, UPDATE ON restart_sequence TO :"app_role";
GRANT SELECT, INSERT         ON restart_sequence_event TO :"app_role";

GRANT USAGE, SELECT ON SEQUENCE restart_sequence_id_seq TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE restart_sequence_event_id_seq TO :"app_role";

-- Explicitly not granted, and the omission is the point:
--   DELETE on either table
--   UPDATE on restart_sequence_event
REVOKE DELETE ON restart_sequence        FROM :"app_role";
REVOKE DELETE ON restart_sequence_event  FROM :"app_role";
REVOKE UPDATE ON restart_sequence_event  FROM :"app_role";

COMMIT;

-- Verify the append-only guarantee holds, as the application role:
--   UPDATE restart_sequence_event SET message = 'tampered';  -- must fail
--   DELETE FROM restart_sequence_event;                      -- must fail
