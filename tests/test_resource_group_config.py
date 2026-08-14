"""The configured resource group tree, and reconciling it with JMX (FR-WL-07).

Two sources answer two different questions. The store says what is configured;
JMX says what has admitted a query. Before D-010 moved the configuration into a
database TMS only had the second, and an absent MBean was ambiguous - "no
traffic yet" and "jmxExport is off" looked identical. These tests are mostly
about keeping those apart.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.api.permissions import Principal  # noqa: E402
from tms.api.services import TmsService  # noqa: E402
from tms.collector.resourcegroups import (  # noqa: E402
    STATUS_HIDDEN,
    STATUS_IDLE,
    STATUS_RUNNING,
    STATUS_UNKNOWN,
    STATUS_UNMANAGED,
    reconcile,
)
from tms.collector.snapshot import (  # noqa: E402
    KIND_RESOURCE_GROUPS,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.core.audit import AuditGuard, InMemoryAuditRepository  # noqa: E402
from tms.core.config import build_config  # noqa: E402
from tms.ops.config_store import ResourceGroupStore  # noqa: E402

VIEWER = Principal("watcher", ["viewer"], ip="10.0.0.9")


# --------------------------------------------------------------------- fakes


class FakeCursor:
    def __init__(self, results):
        self._results = list(results)
        self._current = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._current = self._results.pop(0) if self._results else []

    def fetchall(self):
        return self._current


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor


class FakeStore(ResourceGroupStore):
    """Real query building and row mapping; only the socket is fake."""

    def __init__(self, groups=(), selectors=(), connect_error=None):
        super().__init__("postgresql://u:p@h/d", "trino_resource_groups")
        self._results = [list(groups), list(selectors)]
        self._connect_error = connect_error

    def _connect(self):
        if self._connect_error is not None:
            raise self._connect_error
        return FakeConnection(FakeCursor(self._results))


# resource_group_id, name, parent, soft_memory_limit, soft_concurrency_limit,
# hard_concurrency_limit, max_queued, scheduling_policy, scheduling_weight,
# jmx_export, soft_cpu_limit, hard_cpu_limit, hard_physical_data_scan_limit
GLOBAL = (1, "global", None, "80%", None, 100, 1000, None, None, True, None, None, None)
USER_LEAF = (2, "${USER}", 1, "30%", None, 8, 100, None, None, False, None, None, None)
ADMIN = (3, "admin", None, None, None, 20, 100, None, None, True, None, None, None)

# id, priority, resource_group_id, user_regex, user_group_regex, source_regex,
# query_type, client_tags, original_user_regex, authenticated_user_regex
SEL_ADMIN = (10, 20, 3, "^datalake\\.admin$", None, None, None, None, None, None)
SEL_CATCH_ALL = (11, 10, 2, None, None, None, None, None, None, None)


class LoadTest(unittest.TestCase):
    def test_parent_ids_become_dotted_paths(self):
        """Trino stores parent ids; everything else in TMS speaks dotted paths."""
        tree = FakeStore(groups=[GLOBAL, USER_LEAF, ADMIN]).load_configured("cluster1")
        self.assertEqual(
            {"global", "global.${USER}", "admin"},
            {group["id"] for group in tree.groups})
        leaf = next(g for g in tree.groups if g["name"] == "${USER}")
        self.assertEqual(1, leaf["depth"])

    def test_selectors_carry_the_group_they_send_to(self):
        tree = FakeStore(groups=[GLOBAL, USER_LEAF, ADMIN],
                         selectors=[SEL_ADMIN, SEL_CATCH_ALL]).load_configured("cluster1")
        self.assertEqual(["admin", "global.${USER}"],
                         [s["target"] for s in tree.selectors])

    def test_a_selector_with_no_conditions_is_the_catch_all(self):
        tree = FakeStore(groups=[GLOBAL, USER_LEAF, ADMIN],
                         selectors=[SEL_ADMIN, SEL_CATCH_ALL]).load_configured("cluster1")
        self.assertIsNotNone(tree.catch_all)
        self.assertEqual("global.${USER}", tree.catch_all["target"])
        admin_selector = tree.selectors[0]
        self.assertFalse(admin_selector["catch_all"])
        self.assertEqual({"user_regex": "^datalake\\.admin$"}, admin_selector["matchers"])

    def test_a_tree_without_a_catch_all_is_reported_as_such(self):
        """V10 - Trino 477 does not document what happens to an unmatched query."""
        tree = FakeStore(groups=[GLOBAL, ADMIN],
                         selectors=[SEL_ADMIN]).load_configured("cluster1")
        self.assertIsNone(tree.catch_all)
        self.assertFalse(tree.as_payload()["has_catch_all"])

    def test_no_rows_says_which_environment_was_asked_for(self):
        tree = FakeStore().load_configured("cluster2")
        self.assertIn("cluster2", tree.error)
        self.assertIn("node.environment", tree.advice)

    def test_a_cluster_without_node_environment_is_named_as_the_problem(self):
        tree = FakeStore(groups=[GLOBAL]).load_configured("")
        self.assertIn("node_environment", tree.advice)
        self.assertEqual([], tree.groups)

    def test_an_unreachable_store_does_not_raise(self):
        tree = FakeStore(connect_error=OSError("connection refused")).load_configured("c1")
        self.assertEqual([], tree.groups)
        self.assertIn("unreachable", tree.error.lower())

    def test_a_missing_parent_does_not_recurse_forever(self):
        """The schema is Trino's, so a broken row cannot be assumed away."""
        orphan = (9, "orphan", 404, None, None, 1, 1, None, None, False, None, None, None)
        tree = FakeStore(groups=[orphan]).load_configured("cluster1")
        self.assertEqual(["orphan"], [g["id"] for g in tree.groups])


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.configured = FakeStore(
            groups=[GLOBAL, USER_LEAF, ADMIN]).load_configured("cluster1").groups

    def test_a_group_with_an_mbean_is_running(self):
        rows, _ = reconcile(self.configured, [{"id": "global", "running": 3, "queued": 1}])
        row = next(r for r in rows if r["id"] == "global")
        self.assertEqual(STATUS_RUNNING, row["status"])
        self.assertEqual(3, row["running"])

    def test_exported_but_unseen_is_idle_and_unexported_is_hidden(self):
        """The distinction TMS could not draw before the store existed."""
        rows, _ = reconcile(self.configured, [])
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(STATUS_IDLE, by_id["admin"]["status"])
        self.assertEqual(STATUS_HIDDEN, by_id["global.${USER}"]["status"])

    def test_without_collection_nothing_is_called_idle(self):
        """No MBean is only evidence of idleness if someone was looking."""
        rows, unmanaged = reconcile(self.configured, [], live_available=False)
        self.assertEqual({STATUS_UNKNOWN}, {row["status"] for row in rows})
        self.assertEqual([], unmanaged)

    def test_an_mbean_with_no_row_behind_it_is_surfaced(self):
        rows, unmanaged = reconcile(self.configured, [{"id": "legacy.batch", "running": 2}])
        self.assertEqual(["legacy.batch"], [row["id"] for row in unmanaged])
        self.assertEqual(STATUS_UNMANAGED, unmanaged[0]["status"])
        self.assertNotIn("legacy.batch", [row["id"] for row in rows])


class ServiceTest(unittest.TestCase):
    def build(self, store=None, workload_enabled=True, live=None):
        config = build_config({
            "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid",
                          "expected_workers": 11, "node_environment": "cluster1"}],
            "trino": {"user": "u", "password": "p"},
            "database": {"url": "postgresql://u:p@h/d"},
            "workload": {"enabled": workload_enabled},
            "resource_groups": {"enabled": store is not None},
        })
        snapshots = InMemorySnapshotRepository()
        if live is not None:
            snapshots.save(Snapshot("prod-a", KIND_RESOURCE_GROUPS, utcnow(),
                                    payload={"groups": live}))
        audit = InMemoryAuditRepository()
        return TmsService(config=config, repository=snapshots,
                          audit_guard=AuditGuard(audit), audit_repository=audit,
                          trino_clients={}, config_store=store)

    def test_without_a_store_the_screen_says_why(self):
        service = self.build(store=None)
        data = service.get_resource_group_config(VIEWER, "prod-a")["data"]
        self.assertFalse(data["enabled"])
        self.assertIn("resource_groups.enabled", data["unavailable_reason"])

    def test_the_tree_is_read_for_this_cluster_environment(self):
        store = FakeStore(groups=[GLOBAL, USER_LEAF, ADMIN], selectors=[SEL_CATCH_ALL])
        data = self.build(store=store, live=[{"id": "global", "running": 2}]) \
            .get_resource_group_config(VIEWER, "prod-a")["data"]
        self.assertEqual("cluster1", data["environment"])
        self.assertEqual(3, len(data["rows"]))
        self.assertTrue(data["has_catch_all"])
        self.assertTrue(data["live_available"])

    def test_with_workload_off_the_running_column_is_explained(self):
        """Otherwise a blank column reads as "these groups are all idle"."""
        store = FakeStore(groups=[GLOBAL], selectors=[SEL_CATCH_ALL])
        data = self.build(store=store, workload_enabled=False) \
            .get_resource_group_config(VIEWER, "prod-a")["data"]
        self.assertFalse(data["live_available"])
        self.assertIn("workload.enabled", data["live_reason"])
        self.assertEqual(STATUS_UNKNOWN, data["rows"][0]["status"])

    def test_the_configured_tree_shows_even_when_workload_is_off(self):
        """It comes from the store, which does not depend on collection."""
        store = FakeStore(groups=[GLOBAL, ADMIN], selectors=[SEL_CATCH_ALL])
        data = self.build(store=store, workload_enabled=False) \
            .get_resource_group_config(VIEWER, "prod-a")["data"]
        self.assertEqual(2, len(data["rows"]))


if __name__ == "__main__":
    unittest.main()
