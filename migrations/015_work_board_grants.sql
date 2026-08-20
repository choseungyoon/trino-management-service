-- Privileges for 014. GRANTs in 002 are per-table, so each new table extends
-- them (same pattern as 005, 011 and 013).
--
-- Run as the database owner AFTER 014.

\set app_role 'tms_app'

BEGIN;

-- Items move between statuses and get their bodies edited, so UPDATE. No
-- DELETE: an item that should not have been raised is closed as `dropped`,
-- which leaves the reason someone raised it visible.
GRANT SELECT, INSERT, UPDATE ON work_item TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE work_item_id_seq TO :"app_role";

-- Comments and events are append-only.
GRANT SELECT, INSERT ON work_item_comment TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE work_item_comment_id_seq TO :"app_role";
GRANT SELECT, INSERT ON work_item_event TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE work_item_event_id_seq TO :"app_role";

COMMIT;
