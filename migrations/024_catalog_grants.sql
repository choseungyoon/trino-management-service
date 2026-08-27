-- Privileges for 023. GRANTs in 002 are per-table, so each new table extends
-- them (same pattern as 005, 011, 013, 015, 017, 019 and 021).
--
-- Run as the database owner AFTER 023.

\set app_role 'tms_app'

BEGIN;

-- A draft is configuration: it says what a catalog should be, not what
-- happened. DELETE is allowed, the same as a benchmark query set.
GRANT SELECT, INSERT, UPDATE, DELETE ON catalog_definition TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE catalog_definition_id_seq TO :"app_role";

-- ⛔ A deployment is evidence: what was put on which cluster, by whom, why.
-- INSERT and UPDATE only - UPDATE so a run can be finished, never DELETE.
-- The row for a catalog that broke a cluster is the one somebody will need
-- most, and it must not be removable by the account the application uses.
GRANT SELECT, INSERT, UPDATE ON catalog_deployment TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE catalog_deployment_id_seq TO :"app_role";

COMMIT;
