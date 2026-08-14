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
-- The other half of FR-WL-08 lives in a different schema, and deliberately in
-- a different role (DESIGN_WL07.md H-1).
--
-- Trino's coordinator reads `trino_resource_groups` with a read-only account.
-- TMS writes it with its own, so "who changed this group" is answerable at the
-- database session level and not only from the audit table. Run this once,
-- substituting your own password, and set it as
-- `resource_groups.write_url` (or TMS_RESOURCE_GROUP_WRITE_URL):
--
--   CREATE ROLE tms_rg_writer WITH LOGIN PASSWORD '<...>';
--   GRANT USAGE ON SCHEMA trino_resource_groups TO tms_rg_writer;
--   GRANT SELECT, INSERT, UPDATE, DELETE
--       ON ALL TABLES IN SCHEMA trino_resource_groups TO tms_rg_writer;
--   GRANT USAGE, SELECT ON ALL SEQUENCES
--       IN SCHEMA trino_resource_groups TO tms_rg_writer;
--
-- ⛔ Do not grant this role anything in `public`. It exists to touch the tables
-- Trino reads to admit queries, and nothing else.
-- ---------------------------------------------------------------------------
