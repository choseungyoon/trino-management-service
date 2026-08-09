-- 008 — allow the `fleet` snapshot kind.
--
-- FR-FL-01 stores one row per cluster holding every node's self-reported
-- `/v1/info`. It needs a new `kind`, and the CHECK constraint written by 003
-- would otherwise reject every write.
--
-- ⛔ That rejection is *quiet*: the collector logs the failure and carries on,
-- so the symptom is a screen that stays empty forever with nothing obviously
-- broken. It happened once already, which is why the code-side whitelist
-- (tms/collector/snapshot.py ALLOWED_KINDS) now has a test comparing it to the
-- last definition across all migrations.
--
-- Run as the schema owner:
--   psql -h <db-host> -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/008_snapshot_kind_fleet.sql

BEGIN;

ALTER TABLE collector_snapshot
    DROP CONSTRAINT IF EXISTS collector_snapshot_kind_valid;

ALTER TABLE collector_snapshot
    ADD CONSTRAINT collector_snapshot_kind_valid CHECK (
        kind IN ('queries', 'jmx', 'info', 'health', 'resource_groups',
                 'gateway', 'fleet')
    );

COMMIT;
