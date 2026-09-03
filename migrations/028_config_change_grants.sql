-- Privileges for 027. GRANTs in 002 are per-table, so each new table extends
-- them. Run as the database owner AFTER 027.

\set app_role 'tms_app'

BEGIN;

-- A draft is configuration and may be deleted.
GRANT SELECT, INSERT, UPDATE, DELETE ON config_change TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE config_change_id_seq TO :"app_role";

-- ⛔ A deployment is evidence. No DELETE: the row for the change that stopped
-- a cluster booting is the one that has to survive the tidy-up afterwards.
-- UPDATE only because the row is written when the run starts and completed
-- when it ends.
GRANT SELECT, INSERT, UPDATE ON config_deployment TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE config_deployment_id_seq TO :"app_role";

COMMIT;
