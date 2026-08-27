-- 022 — the `config` snapshot kind (FR-CO-01 · FR-FD-01, D-018 §1).
--
-- What each node has in its `etc/` directory, collected by a read-only
-- playbook on request. ⛔ On request, not on a timer: a scan opens an SSH
-- connection to every node in the cluster, and the answer only changes when
-- somebody changes it.
--
-- ⛔ What is stored is deliberately not everything that is on the node.
-- `etc/catalog/*.properties` holds `connection-password`, so catalogs are kept
-- as checksums only, and any value whose key reads like a credential is
-- replaced before it is written. See src/tms/ops/configscan.py.
--
-- Same DROP-and-recreate shape as 008: the constraint has to name every kind
-- that exists at this point, so re-running an earlier migration after this one
-- removes `config` and the collector's writes start failing silently.

BEGIN;

ALTER TABLE collector_snapshot
    DROP CONSTRAINT IF EXISTS collector_snapshot_kind_valid;

ALTER TABLE collector_snapshot
    ADD CONSTRAINT collector_snapshot_kind_valid CHECK (
        kind IN ('queries', 'jmx', 'info', 'health', 'resource_groups',
                 'gateway', 'fleet', 'config')
    );

COMMIT;
