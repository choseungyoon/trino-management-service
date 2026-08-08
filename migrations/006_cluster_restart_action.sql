-- 006 — allow the CLUSTER_RESTART audit action (R3 FR-CO-02).
--
-- 001 pinned `audit_action.action_type` to the five R1 actions. The safe
-- restart sequence records every step it takes under CLUSTER_RESTART, with the
-- step name in `details`.
--
-- Unlike the snapshot-kind constraint in 003, this one would fail loudly
-- rather than silently: `AuditGuard.action()` refuses an unknown type before
-- touching storage, and a write action with no audit is refused outright
-- (AU1). So the failure mode here is "restarts cannot be started", not
-- "restarts happen unrecorded" — which is the right way round.
--
-- Run as the schema owner:
--   psql -h <db-host> -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/006_cluster_restart_action.sql

BEGIN;

ALTER TABLE audit_action
    DROP CONSTRAINT IF EXISTS audit_action_type_valid;

ALTER TABLE audit_action
    ADD CONSTRAINT audit_action_type_valid CHECK (
        action_type IN (
            'QUERY_KILL',
            'HEALTH_TEST_TOGGLE',
            'HEALTH_ROLLUP_TOGGLE',
            'HEALTH_THRESHOLD_CHANGE',
            'AUDIT_EXPORT',
            'CLUSTER_RESTART'
        )
    );

COMMIT;
