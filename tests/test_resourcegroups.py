"""Tests for resource group collection (FR-WORKLOAD).

The attribute names and shapes here were measured on a local Trino 477 with a
three-level hierarchy (global -> adhoc -> dashboard) on 2026-08-08, including
the throttled case: squeezing `hardConcurrencyLimit` to 1 and submitting three
queries produced RunningQueries=1, QueuedQueries=2. Those exact numbers appear
below.

The bottleneck diagnosis is the point of the feature - "this group is slow" and
"this group is capped" call for completely different responses - so most of
these tests are about not crying wolf.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.clients.errors import TrinoForbidden, TrinoUnavailable  # noqa: E402
from tms.collector.resourcegroups import (  # noqa: E402
    CONCURRENCY_CAPPED,
    GROUP_MBEAN_PREFIX,
    MEMORY_CAPPED,
    REJECTING,
    build_tree,
    collect,
    group_path,
    rollup,
    summarise_group,
)

UNLIMITED = 9223372036854775807


def attrs(**overrides):
    base = {
        "RunningQueries": 0, "QueuedQueries": 0, "WaitingQueuedQueries": 0,
        "HardConcurrencyLimit": 100, "SoftConcurrencyLimit": 100,
        "MaxQueuedQueries": 1000,
        "SoftMemoryLimitBytes": UNLIMITED, "HardCpuLimitMillis": UNLIMITED,
        "MemoryUsageBytes": 0, "CpuUsageMillis": 0,
        "PhysicalInputDataUsageBytes": 0,
        "SchedulingPolicy": "FAIR", "SchedulingWeight": 1,
    }
    base.update(overrides)
    return base


def group(path, **overrides):
    return summarise_group(path, attrs(**overrides))


class FakeClient:
    def __init__(self, names=None, mbeans=None, raise_on_list=None, raise_on=None):
        self._names = names or []
        self._mbeans = mbeans or {}
        self._raise_on_list = raise_on_list
        self._raise_on = raise_on or {}

    def list_mbean_names(self):
        if self._raise_on_list:
            raise self._raise_on_list
        return self._names

    def get_mbean(self, object_name):
        if object_name in self._raise_on:
            raise self._raise_on[object_name]
        return self._mbeans.get(object_name, {})


def mbean_name(dotted):
    return GROUP_MBEAN_PREFIX + dotted


class GroupPathTest(unittest.TestCase):
    def test_dotted_name_becomes_a_path(self):
        self.assertEqual(["global", "adhoc", "dashboard"],
                         group_path(mbean_name("global.adhoc.dashboard")))

    def test_root_group(self):
        self.assertEqual(["global"], group_path(mbean_name("global")))

    def test_other_mbeans_are_ignored(self):
        for name in ("java.lang:type=Memory",
                     "trino.execution.resourcegroups:name=InternalResourceGroupManager",
                     "trino.execution:name=QueryManager"):
            self.assertIsNone(group_path(name), name)


class DiagnoseTest(unittest.TestCase):
    """Measured live: hardConcurrencyLimit=1 with 3 queries submitted."""

    def test_measured_throttled_case_is_flagged(self):
        observed = group(["global", "adhoc", "dashboard"],
                         RunningQueries=1, QueuedQueries=2, WaitingQueuedQueries=2,
                         HardConcurrencyLimit=1, MaxQueuedQueries=10)
        self.assertEqual(CONCURRENCY_CAPPED, observed["bottleneck"])

    def test_at_the_limit_with_an_empty_queue_is_not_a_bottleneck(self):
        """Working exactly as configured. Flagging it trains people to ignore
        the one screen that explains a slow cluster."""
        self.assertIsNone(group(["g"], RunningQueries=100, QueuedQueries=0,
                                HardConcurrencyLimit=100)["bottleneck"])

    def test_a_full_queue_outranks_throttling(self):
        """Past maxQueued Trino rejects outright - worse than waiting."""
        self.assertEqual(REJECTING,
                         group(["g"], RunningQueries=1, QueuedQueries=10,
                               HardConcurrencyLimit=1,
                               MaxQueuedQueries=10)["bottleneck"])

    def test_memory_pressure_is_reported_when_queries_wait(self):
        self.assertEqual(MEMORY_CAPPED,
                         group(["g"], RunningQueries=1, QueuedQueries=3,
                               HardConcurrencyLimit=100,
                               SoftMemoryLimitBytes=1000,
                               MemoryUsageBytes=1200)["bottleneck"])

    def test_idle_group_is_never_flagged(self):
        self.assertIsNone(group(["g"])["bottleneck"])

    def test_unlimited_sentinels_are_not_treated_as_limits(self):
        """Long.MAX_VALUE means 'no limit'. Comparing against it would mark
        every healthy group as capped."""
        summary = group(["g"], RunningQueries=5, QueuedQueries=5,
                        HardConcurrencyLimit=UNLIMITED,
                        MaxQueuedQueries=UNLIMITED,
                        CpuUsageMillis=10 ** 12)
        self.assertIsNone(summary["hard_concurrency_limit"])
        self.assertIsNone(summary["max_queued"])
        self.assertIsNone(summary["bottleneck"])


class SummariseTest(unittest.TestCase):
    def test_identity_fields(self):
        summary = group(["global", "adhoc", "dashboard"])
        self.assertEqual("global.adhoc.dashboard", summary["id"])
        self.assertEqual("dashboard", summary["name"])
        self.assertEqual(2, summary["depth"])

    def test_missing_attributes_do_not_raise(self):
        summary = summarise_group(["g"], {})
        self.assertEqual(0, summary["running"])
        self.assertIsNone(summary["hard_concurrency_limit"])
        self.assertIsNone(summary["bottleneck"])


class TreeTest(unittest.TestCase):
    def test_three_levels_nest(self):
        tree = build_tree([group(["global"]), group(["global", "adhoc"]),
                           group(["global", "adhoc", "dashboard"])])
        self.assertEqual(1, len(tree))
        self.assertEqual("global.adhoc", tree[0]["children"][0]["id"])
        self.assertEqual("global.adhoc.dashboard",
                         tree[0]["children"][0]["children"][0]["id"])

    def test_orphan_is_promoted_not_dropped(self):
        """A busy child whose parent was not exported must still appear -
        dropping it hides exactly what the operator is looking for."""
        tree = build_tree([group(["global", "adhoc"], RunningQueries=7)])
        self.assertEqual(1, len(tree))
        self.assertEqual("global.adhoc", tree[0]["id"])

    def test_siblings_are_ordered_and_kept(self):
        tree = build_tree([group(["global"]), group(["global", "etl"]),
                           group(["global", "adhoc"])])
        self.assertEqual(["global.adhoc", "global.etl"],
                         [c["id"] for c in tree[0]["children"]])


class RollupTest(unittest.TestCase):
    def test_totals_and_blocked_list(self):
        groups = [
            group(["global"], RunningQueries=3),
            group(["global", "a"], RunningQueries=1, QueuedQueries=4,
                  HardConcurrencyLimit=1),
        ]
        summary = rollup(groups)
        self.assertEqual(2, summary["groups"])
        self.assertEqual(4, summary["running"])
        self.assertEqual(4, summary["queued"])
        self.assertEqual(1, summary["blocked_groups"])
        self.assertEqual("global.a", summary["blocked"][0]["id"])

    def test_blocked_groups_are_ordered_by_queue_depth(self):
        groups = [
            group(["a"], RunningQueries=1, QueuedQueries=2, HardConcurrencyLimit=1),
            group(["b"], RunningQueries=1, QueuedQueries=9, HardConcurrencyLimit=1),
        ]
        self.assertEqual(["b", "a"],
                         [item["id"] for item in rollup(groups)["blocked"]])


class CollectTest(unittest.TestCase):
    def test_collects_and_nests(self):
        names = [mbean_name("global"), mbean_name("global.adhoc"),
                 "java.lang:type=Memory"]
        client = FakeClient(names=names, mbeans={n: attrs() for n in names})
        result = collect(client)
        self.assertIsNone(result.error)
        self.assertEqual(2, len(result.groups), "non-group MBeans must be ignored")
        self.assertEqual(1, len(result.tree))

    def test_result_is_never_marked_complete(self):
        """Lazily-created groups mean the collected set is never the configured
        set. The UI has to say so."""
        client = FakeClient(names=[mbean_name("global")],
                            mbeans={mbean_name("global"): attrs()})
        self.assertFalse(collect(client).complete)
        self.assertFalse(collect(client).as_payload()["complete"])

    def test_no_group_mbeans_explains_jmxexport(self):
        """The likeliest cause is a missing jmxExport, not an idle cluster."""
        result = collect(FakeClient(names=["java.lang:type=Memory"]))
        self.assertEqual([], result.groups)
        self.assertIn("jmxExport", result.advice)

    def test_registry_failure_is_reported_with_advice(self):
        result = collect(FakeClient(raise_on_list=TrinoForbidden("403")))
        self.assertIn("TrinoForbidden", result.error)
        self.assertIn("rules.json", result.advice)

    def test_one_bad_mbean_does_not_lose_the_others(self):
        good, bad = mbean_name("global"), mbean_name("global.adhoc")
        client = FakeClient(names=[good, bad], mbeans={good: attrs()},
                            raise_on={bad: TrinoUnavailable("boom")})
        result = collect(client)
        self.assertEqual(1, len(result.groups))
        self.assertIn("1 of 2", result.error)


if __name__ == "__main__":
    unittest.main()
