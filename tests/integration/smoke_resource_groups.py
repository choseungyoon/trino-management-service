"""Resource group editing against a real PostgreSQL (FR-WL-08/09/10).

Everything here is a thing a fake cannot tell you:

* whether ``ON DELETE CASCADE`` actually fires, and how far it reaches;
* whether a rejected change really leaves the table untouched, or merely
  returned an error after writing;
* whether the revision row and the change land together;
* whether the schema Trino created accepts what TMS writes into it - column
  types and lengths measured in TRINO_VERIFIED.md T1-4-1 are only a claim until
  something inserts a row.

Not named ``test_*`` on purpose: `pytest tests/` must stay infrastructure-free
(same convention as ``smoke_api_postgres.py``).

Run:
    export TMS_SMOKE_DSN='postgresql://tms_admin@localhost:5433/tms_local'
    <venv>/bin/python -m unittest tests.integration.smoke_resource_groups -v
"""

import os
import sys
import unittest
import uuid

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "src"),
)

DSN = os.environ.get("TMS_SMOKE_DSN")

try:
    import psycopg  # noqa: F401

    HAVE_PSYCOPG = True
except ImportError:  # pragma: no cover
    HAVE_PSYCOPG = False

from tms.ops.config_store import ChangeRejected, ResourceGroupStore  # noqa: E402

SCHEMA = "trino_resource_groups"
ENV = "smoke_env"


@unittest.skipUnless(DSN and HAVE_PSYCOPG, "set TMS_SMOKE_DSN and install psycopg")
class ResourceGroupStoreSmokeTest(unittest.TestCase):
    def setUp(self):
        self.store = ResourceGroupStore(DSN, SCHEMA)
        self._wipe()
        self._seed()

    def tearDown(self):
        self._wipe()

    # ---------------------------------------------------------------- setup

    def _sql(self, statement, params=None, fetch=False):
        import psycopg

        with psycopg.connect(DSN, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, params)
                return cursor.fetchall() if fetch else None

    def _wipe(self):
        self._sql("DELETE FROM {}.resource_groups WHERE environment = %s".format(SCHEMA),
                  (ENV,))
        self._sql("DELETE FROM resource_group_revision WHERE environment = %s", (ENV,))

    def _seed(self):
        """The tree the setup script produces, minus the cluster it belongs to."""
        rows = self._sql(
            "INSERT INTO {}.resource_groups"
            " (name, environment, soft_memory_limit, hard_concurrency_limit,"
            "  max_queued, jmx_export)"
            " VALUES ('global', %s, '80%%', 100, 1000, true)"
            " RETURNING resource_group_id".format(SCHEMA), (ENV,), fetch=True)
        self.global_id = rows[0][0]

        rows = self._sql(
            "INSERT INTO {}.resource_groups"
            " (name, environment, parent, soft_memory_limit, hard_concurrency_limit,"
            "  max_queued, jmx_export)"
            " VALUES ('${{USER}}', %s, %s, '30%%', 8, 100, false)"
            " RETURNING resource_group_id".format(SCHEMA),
            (ENV, self.global_id), fetch=True)
        self.leaf_id = rows[0][0]

        self._sql(
            "INSERT INTO {}.selectors (resource_group_id, priority)"
            " VALUES (%s, 10)".format(SCHEMA), (self.leaf_id,))

    def _change(self, method, *args, **kwargs):
        return getattr(self.store, method)(
            ENV, *args, actor="smoke", reason="integration test",
            request_id=str(uuid.uuid4()), **kwargs)

    def _groups(self):
        return {g["id"]: g for g in self.store.load_configured(ENV).groups}

    def _revisions(self):
        return self._sql(
            "SELECT kind, target, reason FROM resource_group_revision"
            " WHERE environment = %s ORDER BY id", (ENV,), fetch=True)

    # ----------------------------------------------------------------- read

    def test_the_seeded_tree_reads_back_with_dotted_paths(self):
        groups = self._groups()
        self.assertEqual({"global", "global.${USER}"}, set(groups))
        self.assertEqual(1, groups["global.${USER}"]["depth"])
        self.assertFalse(groups["global.${USER}"]["jmx_export"])

    # ---------------------------------------------------------------- write

    def test_an_update_lands_and_records_a_revision_in_the_same_breath(self):
        result = self._change("update_group", self.leaf_id, {"hard_concurrency_limit": 12})
        self.assertEqual(12, self._groups()["global.${USER}"]["hard_concurrency_limit"])
        self.assertIsNotNone(result.revision_id)
        # The history records the dotted path, not the primary key: "group
        # update 61" is a row nobody can act on.
        self.assertEqual([("group_update", "global.${USER}", "integration test")],
                         self._revisions())

    def test_the_revision_holds_both_sides_of_the_change(self):
        self._change("update_group", self.leaf_id, {"max_queued": 250})
        rows = self._sql(
            "SELECT tree_before, tree_after FROM resource_group_revision"
            " WHERE environment = %s", (ENV,), fetch=True)
        before, after = rows[0]
        leaf_before = next(g for g in before["groups"] if g["name"] == "${USER}")
        leaf_after = next(g for g in after["groups"] if g["name"] == "${USER}")
        self.assertEqual(100, leaf_before["max_queued"])
        self.assertEqual(250, leaf_after["max_queued"])

    def test_a_rejected_change_leaves_the_table_exactly_as_it_was(self):
        """The whole point of validating inside the transaction."""
        with self.assertRaises(ChangeRejected):
            self._change("update_group", self.leaf_id, {"hard_concurrency_limit": 0})
        self.assertEqual(8, self._groups()["global.${USER}"]["hard_concurrency_limit"])
        self.assertEqual([], self._revisions(), "no revision for a change that failed")

    def test_removing_the_last_catch_all_is_refused_by_the_tree_it_would_leave(self):
        """V10 lives in validation, so it holds no matter which call site asks."""
        selectors = self.store.load_configured(ENV).selectors
        with self.assertRaises(ChangeRejected) as caught:
            self._change("delete_selector", selectors[0]["id"])
        self.assertIn("catch-all", str(caught.exception))
        self.assertEqual(1, len(self.store.load_configured(ENV).selectors))

    # -------------------------------------------------------------- cascade

    def test_deleting_a_parent_takes_the_subtree_and_its_selectors(self):
        """Measured rather than assumed: both foreign keys carry ON DELETE
        CASCADE, which is why the screen lists what goes before it goes."""
        impact = self.store.deletion_impact(ENV, self.global_id)
        self.assertEqual({"global", "global.${USER}"},
                         {g["id"] for g in impact["groups"]})
        self.assertEqual(1, len(impact["selectors"]))

        # One row deleted, three gone. Emptying an environment is allowed -
        # nothing configured is a different state from misconfigured - but the
        # restart gate then refuses to stop the cluster, because a coordinator
        # started against this would come up with no resource groups at all.
        self._change("delete_group", self.global_id)
        tree = self.store.load_configured(ENV)
        self.assertEqual([], tree.groups)
        self.assertEqual([], tree.selectors, "the selector cascaded away too")
        self.assertIs(False, self.store.probe(ENV).ready)

    def test_a_leaf_delete_removes_its_selector_too(self):
        # Give the tree a second catch-all so removing the leaf stays legal.
        self._sql("INSERT INTO {}.selectors (resource_group_id, priority)"
                  " VALUES (%s, 1)".format(SCHEMA), (self.global_id,))
        self._change("delete_group", self.leaf_id)
        tree = self.store.load_configured(ENV)
        self.assertEqual({"global"}, {g["id"] for g in tree.groups})
        self.assertEqual(1, len(tree.selectors), "the leaf's selector cascaded away")

    # --------------------------------------------------------------- revert

    def test_revert_restores_the_tree_and_appends_rather_than_erases(self):
        first = self._change("update_group", self.leaf_id, {"hard_concurrency_limit": 12})
        self._change("update_group", self.leaf_id, {"hard_concurrency_limit": 99})
        self.assertEqual(99, self._groups()["global.${USER}"]["hard_concurrency_limit"])

        self._change("revert", first.revision_id)
        self.assertEqual(8, self._groups()["global.${USER}"]["hard_concurrency_limit"],
                         "back to what the first change found")
        kinds = [row[0] for row in self._revisions()]
        self.assertEqual(["group_update", "group_update", "revert"], kinds)

    def test_revert_rebuilds_selectors_against_the_new_row_ids(self):
        """Restoring deletes and re-inserts, so every id changes underneath."""
        first = self._change("update_group", self.leaf_id, {"max_queued": 250})
        self._change("revert", first.revision_id)
        tree = self.store.load_configured(ENV)
        self.assertEqual(1, len(tree.selectors))
        self.assertEqual("global.${USER}", tree.selectors[0]["target"])
        self.assertTrue(tree.selectors[0]["catch_all"])

    # ---------------------------------------------------------------- probe

    def test_the_restart_gate_sees_this_environment_as_ready(self):
        probe = self.store.probe(ENV)
        self.assertIs(True, probe.ready, probe.detail)

    def test_an_environment_with_no_rows_blocks_a_restart(self):
        probe = self.store.probe("no_such_environment")
        self.assertIs(False, probe.ready)


if __name__ == "__main__":
    unittest.main()
