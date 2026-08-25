-- Privileges for 018. GRANTs in 002 are per-table, so each new table extends
-- them (same pattern as 005, 011, 013, 015 and 017).
--
-- Run as the database owner AFTER 018.

\set app_role 'tms_app'

BEGIN;

-- DELETE, unlike every other table TMS writes. A query set is configuration,
-- not evidence: removing a query is the same act as deleting the lines from
-- the YAML file this replaced. The evidence is elsewhere and untouched -
-- `benchmark_result` keeps the measurements and `benchmark_run.queries` keeps
-- the statement that produced them, both by value, neither by reference.
GRANT SELECT, INSERT, UPDATE, DELETE ON benchmark_query_set TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE benchmark_query_set_id_seq TO :"app_role";

GRANT SELECT, INSERT, UPDATE, DELETE ON benchmark_query TO :"app_role";
GRANT USAGE, SELECT ON SEQUENCE benchmark_query_id_seq TO :"app_role";

COMMIT;
