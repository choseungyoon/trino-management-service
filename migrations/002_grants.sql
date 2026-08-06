-- TMS schema, revision 002: privileges for the application role.
--
-- Run as the database owner AFTER 001_init.sql.
-- Replace tms_app if your application role is named differently.
--
-- The append-only guarantee (AUDIT_MODEL.md AU3) is enforced here by never
-- granting UPDATE or DELETE on audit_action and health_event, rather than by
-- granting everything and revoking afterwards. Least privilege by construction
-- survives someone re-running a GRANT ALL later; a REVOKE does not.

\set app_role 'tms_app'

BEGIN;

GRANT CONNECT ON DATABASE :"DBNAME" TO :"app_role";
GRANT USAGE ON SCHEMA public TO :"app_role";

-- ---------------------------------------------------------------------------
-- Append-only: INSERT and SELECT only. No UPDATE. No DELETE. No TRUNCATE.
-- If a future feature appears to need UPDATE here, it is a design error -
-- corrections are new rows, not edits.
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT ON audit_action TO :"app_role";
GRANT SELECT, INSERT ON health_event TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE audit_action_id_seq TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE health_event_id_seq TO :"app_role";

-- ---------------------------------------------------------------------------
-- Mutable working state.
-- collector_snapshot is upserted every poll; health_test_override is edited by
-- administrators (every edit is also written to audit_action).
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON collector_snapshot TO :"app_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON health_test_override TO :"app_role";

COMMIT;

-- ---------------------------------------------------------------------------
-- Verification. Both statements must FAIL when run as the application role;
-- if either succeeds, the append-only guarantee is not in place.
--
--   psql "$TMS_APP_DSN" -c "UPDATE audit_action SET reason = 'x';"
--   psql "$TMS_APP_DSN" -c "DELETE FROM audit_action;"
--
-- Expected: ERROR:  permission denied for table audit_action
-- ---------------------------------------------------------------------------
