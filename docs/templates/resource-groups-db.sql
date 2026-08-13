-- Resource groups for Trino 477, database configuration manager.
--
-- Mirrors docs/templates/resource-group.json. The JSON is the readable spec and
-- the Git-tracked snapshot; once the db manager is enabled Trino no longer reads
-- it, and THIS database is the source of truth.
--
-- RUN ONCE PER CLUSTER. Every row is scoped by `environment`, which Trino matches
-- against `node.environment` in node.properties. The two clusters use different
-- values, so the same script is applied twice with a different :env each time:
--
--   psql -h <host> -U <user> -d <db> -v env=cluster1 -f resource-groups-db.sql
--   psql -h <host> -U <user> -d <db> -v env=cluster2 -f resource-groups-db.sql
--
-- The value that matters is the COORDINATOR's node.environment: resource groups
-- are a coordinator concern, and the workers never read this table.
--
-- PREREQUISITE: the tables must already exist. Trino creates
-- `resource_groups`, `selectors` and `resource_groups_global_properties`
-- automatically on first start with the db manager configured. Do not hand-write
-- the DDL - Trino 477 does not document the schema, so any handmade table is a
-- guess at column types and constraints.
--
-- SCHEMA ISOLATION (D-010): these tables live in the TMS PostgreSQL instance,
-- in their own schema, never alongside the TMS application tables. Trino reads
-- them to decide whether to admit a query, so a TMS migration must have no path
-- to reach them - the TMS migration account is not granted rights on this
-- schema. (The same isolation also avoids a name collision with the resource
-- group tables Gateway 19 leaves behind on upgrade, should this ever move to the
-- Gateway database.)
--
--   CREATE SCHEMA trino_resource_groups;
--   -- and in resource-groups.properties:
--   -- resource-groups.config-db-url=jdbc:postgresql://<host>:5432/<db>?currentSchema=trino_resource_groups
--
-- Run this script with the same search_path, e.g.:
--   psql ... -c 'SET search_path TO trino_resource_groups' -f resource-groups-db.sql
-- or set it per-session below.
--
-- This script is not idempotent: it INSERTs. Run it once per environment against
-- empty tables. To change a value afterwards, UPDATE - the coordinators reload
-- every second and no restart is needed. That is the whole point of db mode.
--
-- Nothing in the database stops a double run. Trino's schema has no unique
-- constraint on (name, parent, environment); the only key is the autoincrement
-- resource_group_id, so a second run silently produces a duplicate tree
-- (verified 2026-08-13, TRINO_VERIFIED.md T1-4-1). Check before re-running:
--
--   SELECT environment, count(*) FROM resource_groups GROUP BY environment;
--
-- Deleting is likewise sharper than it looks: both resource_groups.parent and
-- selectors.resource_group_id are ON DELETE CASCADE, so removing a root group
-- takes its whole subtree and every selector pointing into it.
--
-- Column names are taken verbatim from the Trino 477 documentation example
-- (https://trino.io/docs/477/admin/resource-groups.html).

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------------------
-- Groups
--
-- Sizing assumes the post-upgrade memory layout: -Xmx 400GB and
-- memory.heap-headroom-per-node 60GB per worker, 11 workers, so roughly
-- 3,740GB of cluster memory. Percentages are of CLUSTER memory, not of the
-- parent group, so sibling percentages must not sum past their parent.
--
-- No CPU or physical-data-scan quotas are set. Exceeding one of those does not
-- fail a query - it queues new ones until the quota period rolls, which is up
-- to an hour of a user silently going nowhere. There is no scan-volume data yet
-- (docs/WORKLOAD_PROFILE.md is empty), so any value would be a guess whose
-- failure mode is invisible. Add them once that data exists.
-- ---------------------------------------------------------------------------

-- Root: everything that is not an admin lands under here.
-- Default scheduling policy (fair) is what we want: queued queries run FIFO and
-- sub-groups take turns, which is exactly right when the sub-groups are peers.
INSERT INTO resource_groups
    (name, environment, soft_memory_limit, hard_concurrency_limit, max_queued, jmx_export)
VALUES
    ('global', :'env', '80%', 100, 1000, true);

-- global.${USER} - one group per user, created lazily on that user's first
-- query. 30% caps any single person at roughly a third of the cluster.
--
-- jmx_export is deliberately absent. Turning it on would register one MBean per
-- user: 50 today, 50,000 at the target scale, all of them enumerated on every
-- TMS poll. Individual users are visible on the running-queries screen instead.
INSERT INTO resource_groups
    (name, environment, parent, soft_memory_limit, hard_concurrency_limit, max_queued)
SELECT '${USER}', :'env', resource_group_id, '30%', 8, 100
FROM resource_groups
WHERE name = 'global' AND environment = :'env' AND parent IS NULL;

-- Root: admin. No memory limit - an admin must still be able to work when the
-- cluster is busy. A runaway admin query is still bounded by query.max-memory.
INSERT INTO resource_groups
    (name, environment, hard_concurrency_limit, max_queued, jmx_export)
VALUES
    ('admin', :'env', 20, 100, true);

-- ---------------------------------------------------------------------------
-- Selectors, evaluated in descending order of `priority`.
--
-- user_group_regex is not used: no group provider is configured
-- (etc/group-provider.properties does not exist), so the groups Trino passes in
-- are always empty and any group-based rule would silently never match.
-- ---------------------------------------------------------------------------

-- The dot in datalake.admin is escaped. Unescaped, '.' matches any character,
-- so 'datalake.admin' would also match 'datalakeXadmin'.
INSERT INTO selectors (resource_group_id, user_regex, priority)
SELECT resource_group_id, '^datalake\.admin$', 20
FROM resource_groups
WHERE name = 'admin' AND environment = :'env' AND parent IS NULL;

-- Catch-all, lowest priority, no match conditions. Trino 477 does not document
-- what happens to a query that matches no selector at all, so the safe design is
-- to make that case impossible rather than to find out in production.
INSERT INTO selectors (resource_group_id, priority)
SELECT child.resource_group_id, 10
FROM resource_groups child
JOIN resource_groups parent ON child.parent = parent.resource_group_id
WHERE child.name = '${USER}'
  AND parent.name = 'global'
  AND child.environment = :'env';

COMMIT;

-- ---------------------------------------------------------------------------
-- Verify: three groups and two selectors for this environment.
-- ---------------------------------------------------------------------------

SELECT child.resource_group_id,
       coalesce(parent.name || '.', '') || child.name AS path,
       child.soft_memory_limit,
       child.hard_concurrency_limit,
       child.max_queued,
       child.jmx_export
FROM resource_groups child
LEFT JOIN resource_groups parent ON child.parent = parent.resource_group_id
WHERE child.environment = :'env'
ORDER BY path;

SELECT s.priority, s.user_regex, s.source_regex, g.name AS target_group
FROM selectors s
JOIN resource_groups g ON s.resource_group_id = g.resource_group_id
WHERE g.environment = :'env'
ORDER BY s.priority DESC;
