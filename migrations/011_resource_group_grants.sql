-- Privileges for 010. GRANTs in 002 are per-table, so each new table extends
-- them (same pattern as 005 for the restart sequence).
--
-- Run as the database owner AFTER 010.

\set app_role 'tms_app'

BEGIN;

-- Append-only, like audit_action and health_event: INSERT and SELECT only.
-- Reverting a change writes a new revision; it does not rewrite an old one.
-- Granting UPDATE here would make the history editable, which would make it
-- worthless as a record of what was done to a production cluster.
GRANT SELECT, INSERT ON resource_group_revision TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE resource_group_revision_id_seq TO :"app_role";

COMMIT;

-- ---------------------------------------------------------------------------
-- The other half of FR-WL-08 lives in a different schema: the tables Trino
-- reads to decide whether to admit a query. Run this once, as the owner:
--
--   GRANT USAGE ON SCHEMA trino_resource_groups TO tms_app;
--   GRANT SELECT, INSERT, UPDATE, DELETE
--       ON ALL TABLES IN SCHEMA trino_resource_groups TO tms_app;
--   GRANT USAGE, SELECT ON ALL SEQUENCES
--       IN SCHEMA trino_resource_groups TO tms_app;
--
-- ⛔ `tms_app`, not a separate writer role. The design called for one
-- (DESIGN_WL07.md H-1) and implementation withdrew it: two accounts are two
-- connections, and two connections cannot be one transaction. Sharing the
-- connection is what makes the change and its revision snapshot commit or fail
-- together - a change with no snapshot cannot happen. "Who changed this" is
-- already answered by the audit table.
--
-- Full procedure, including the Trino-side file -> db transition:
-- docs/runbooks/resource-groups-db.md
-- ---------------------------------------------------------------------------
