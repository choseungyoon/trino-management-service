-- Privileges for 025. GRANTs in 002 are per-table, so each new table extends
-- them (same pattern as 005, 011, 013, 015, 017, 019, 021 and 024).
--
-- Run as the database owner AFTER 025.

\set app_role 'tms_app'

BEGIN;

-- DELETE is allowed: the node list is configuration, not evidence. Removing a
-- node says "stop deploying to this host", which is a decision somebody makes
-- and the audit log records. What was *done* to a node lives in
-- audit_action and the deployment tables, and those stay.
GRANT SELECT, INSERT, UPDATE, DELETE ON cluster_node TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE cluster_node_id_seq TO :"app_role";

COMMIT;
