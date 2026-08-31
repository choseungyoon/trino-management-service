-- 025 — the node list, owned by TMS rather than by a file (D-019).
--
-- Workers were maintained by hand in an Ansible inventory: every cluster added
-- and every worker scaled meant editing a file on the server. Now the
-- coordinator is asked instead (`system.runtime.nodes`, reachable since D-012
-- granted ExecuteQuery) and this table is what it fills.
--
-- ⛔ `source` is the whole design. A node TMS discovered and a node somebody
-- typed in are different things:
--
--   discovered  the coordinator reported it. Refreshed on every scan
--   manual      a person added it, because it is down and therefore invisible
--               to discovery - but still has to receive configuration
--
-- ⛔ Discovery adds and refreshes. It never deletes. A node that vanishes from
-- `system.runtime.nodes` is either decommissioned or *down*, and TMS cannot
-- tell those apart. Auto-removing would drop a down node out of every
-- deployment, so it comes back running last month's configuration - which is
-- exactly the drift D-018 exists to catch. Removal is a person's act, with a
-- reason.
--
-- ⛔ The cluster list is NOT here. That is the Gateway's (D-008). This table
-- holds what is *inside* a cluster, which the Gateway knows nothing about.

CREATE TABLE IF NOT EXISTS cluster_node (
    id           BIGSERIAL   PRIMARY KEY,
    cluster      TEXT        NOT NULL,
    -- What the inventory calls it. For a discovered node this is the host part
    -- of its http_uri, because that is the only name the coordinator gives.
    host         TEXT        NOT NULL,
    -- Where TMS and Ansible actually connect. Usually the same as `host`; kept
    -- separate because an inventory alias is often a name only Ansible
    -- resolves, and connecting to it would make a healthy node look dead.
    address      TEXT        NOT NULL,
    role         TEXT        NOT NULL,
    source       TEXT        NOT NULL,

    -- Last time the coordinator reported it. NULL for a node that has only
    -- ever been typed in. This is what makes a decommissioned node obvious
    -- without TMS having to guess.
    last_seen_at TIMESTAMPTZ,
    node_id      TEXT,
    version      TEXT,

    added_by     TEXT        NOT NULL,
    reason       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT cluster_node_role_valid   CHECK (role IN ('coordinator', 'worker')),
    CONSTRAINT cluster_node_source_valid CHECK (source IN ('discovered', 'manual')),
    -- A manual entry is a write, and absolute rule 3 has no exception for it.
    CONSTRAINT cluster_node_manual_has_reason
        CHECK (source <> 'manual' OR btrim(coalesce(reason, '')) <> ''),
    -- One row per host per cluster. Without this a rescan would append a
    -- duplicate on every poll.
    CONSTRAINT cluster_node_unique UNIQUE (cluster, host)
);

CREATE INDEX IF NOT EXISTS cluster_node_cluster_idx ON cluster_node (cluster);

-- ── audit ────────────────────────────────────────────────────────────
--
-- ⛔ DROP and re-CREATE, naming every action that exists at this point.

BEGIN;

ALTER TABLE audit_action DROP CONSTRAINT IF EXISTS audit_action_type_valid;
ALTER TABLE audit_action ADD CONSTRAINT audit_action_type_valid CHECK (
        action_type IN ('QUERY_KILL', 'HEALTH_TEST_TOGGLE', 'HEALTH_ROLLUP_TOGGLE',
                        'HEALTH_THRESHOLD_CHANGE', 'AUDIT_EXPORT',
                        'CLUSTER_RESTART', 'NODE_SHUTDOWN',
                        'RESOURCE_GROUP_CHANGE', 'RESOURCE_GROUP_REVERT',
                        'FLEET_JOB_RUN', 'BENCHMARK_RUN', 'BENCHMARK_QUERY_CHANGE',
                        'BENCHMARK_SCHEDULE_CHANGE',
                        'CATALOG_CHANGE', 'CATALOG_DEPLOY',
                        'CLUSTER_NODE_CHANGE')
    );

COMMIT;
