-- 003 — allow the R2 snapshot kinds.
--
-- 001 pinned `collector_snapshot.kind` to the four kinds R1 collected. R2 adds
-- two more:
--
--   resource_groups  FR-WORKLOAD — the resource group tree, from JMX
--   gateway          FR-GATEWAY  — Gateway backends, joined to TMS clusters
--
-- Without this the collector writes them, PostgreSQL rejects the row, and the
-- collector logs and carries on (storage must never kill the polling loop).
-- The screens then sit empty with no indication that anything is wrong. That is
-- exactly what happened on the local stack, and unit tests could not catch it:
-- the in-memory repository has no constraints.
--
-- Run as the schema owner:
--   psql -h <db-host> -U tms_owner -d tms -v ON_ERROR_STOP=1 -f migrations/003_snapshot_kinds.sql
--
-- Safe to re-run. Adds no columns and rewrites no data.

BEGIN;

ALTER TABLE collector_snapshot
    DROP CONSTRAINT IF EXISTS collector_snapshot_kind_valid;

ALTER TABLE collector_snapshot
    ADD CONSTRAINT collector_snapshot_kind_valid CHECK (
        kind IN ('queries', 'jmx', 'info', 'health', 'resource_groups', 'gateway')
    );

COMMIT;

-- Verify:
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conname = 'collector_snapshot_kind_valid';
--
-- The Gateway snapshot is fleet-scoped rather than per-cluster and is stored
-- with cluster = '*', which the primary key (cluster, kind) already allows.
