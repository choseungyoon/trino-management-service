-- 009 — allow NODE_SHUTDOWN as an audit action type.
--
-- FR-FL-03 (graceful shutdown of a worker) is a write action, so it carries a
-- reason and an audit record like every other one. The whitelist in
-- tms/core/audit.py and this constraint must agree; `tests/test_audit.py`
-- compares the code list against the *last* definition across all migrations.
--
-- Run as the schema owner:
--   psql -h <db-host> -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/009_node_shutdown_action.sql

BEGIN;

ALTER TABLE audit_action
    DROP CONSTRAINT IF EXISTS audit_action_type_valid;

ALTER TABLE audit_action
    ADD CONSTRAINT audit_action_type_valid CHECK (
        action_type IN ('QUERY_KILL', 'HEALTH_TEST_TOGGLE', 'HEALTH_ROLLUP_TOGGLE',
                        'HEALTH_THRESHOLD_CHANGE', 'AUDIT_EXPORT', 'CLUSTER_RESTART',
                        'NODE_SHUTDOWN')
    );

COMMIT;
