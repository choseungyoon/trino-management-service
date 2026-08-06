"""Tests for the collector's polling logic.

The headline case is H-09. With `file` access control, a denied `queries` rule
filters the response to an empty list instead of returning 403, so a busy
cluster and a forbidden one produce byte-identical responses. Without the JMX
cross-check TMS would display "0 running queries" on a fully loaded cluster and
look perfectly healthy doing it.
"""

import os
import sys
import unittest
from datetime import timedelta

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.clients.errors import TrinoForbidden, TrinoUnavailable  # noqa: E402
from tms.clients.trino import NODE_MANAGER_MBEAN, QueryListResult  # noqa: E402
from tms.collector.poller import (  # noqa: E402
    KIND_INFO,
    KIND_JMX,
    KIND_QUERIES,
    ClusterPoller,
    summarise_query,
)
from tms.collector.snapshot import (  # noqa: E402
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)

QUERY_MANAGER = "trino.execution:name=QueryManager"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeClient:
    """Stands in for TrinoClient. Every method can be made to raise."""

    def __init__(self, queries=None, mbeans=None, info=None, response_bytes=1000):
        self.queries = queries if queries is not None else []
        self.mbeans = mbeans or {}
        self.info = info or {"starting": False}
        self.response_bytes = response_bytes
        self.raise_on = {}
        self.calls = []

    def list_queries(self, states=None):
        self.calls.append("list_queries")
        if "list_queries" in self.raise_on:
            raise self.raise_on["list_queries"]
        return QueryListResult(self.queries, self.response_bytes, 0.01)

    def get_mbean(self, object_name):
        self.calls.append("get_mbean:" + object_name)
        if object_name in self.raise_on:
            raise self.raise_on[object_name]
        if "get_mbean" in self.raise_on:
            raise self.raise_on["get_mbean"]
        return self.mbeans.get(object_name, {})

    def get_server_info(self):
        self.calls.append("get_server_info")
        if "get_server_info" in self.raise_on:
            raise self.raise_on["get_server_info"]
        return self.info


def raw_query(query_id="q1", state="RUNNING", elapsed="10.00s", user="analyst", sql="SELECT 1"):
    return {
        "queryId": query_id,
        "state": state,
        "query": sql,
        "resourceGroupId": ["global", "bi"],
        "session": {"user": user, "source": "superset"},
        "queryStats": {
            "elapsedTime": elapsed,
            "queuedTime": "0.50s",
            "totalCpuTime": "20.00s",
            "peakUserMemoryReservation": "8589934592B",
            "physicalInputDataSize": "1048576B",
            "progressPercentage": 42.0,
            "runningDrivers": 5,
            "queuedDrivers": 1,
            "fullyBlocked": False,
        },
    }


def make_poller(client, repository=None, clock=None, **kwargs):
    return ClusterPoller(
        cluster_name="prod-a",
        client=client,
        repository=repository or InMemorySnapshotRepository(),
        clock=clock or FakeClock(),
        **kwargs
    )


class SummariseQueryTest(unittest.TestCase):
    def test_parses_airlift_units(self):
        summary = summarise_query(raw_query(), 4096, 300)
        self.assertEqual(summary["elapsed_ms"], 10000.0)
        self.assertEqual(summary["total_cpu_ms"], 20000.0)
        self.assertEqual(summary["peak_user_memory_bytes"], 8589934592)
        self.assertEqual(summary["physical_input_bytes"], 1048576)

    def test_extracts_user_and_source_from_session(self):
        summary = summarise_query(raw_query(), 4096, 300)
        self.assertEqual(summary["user"], "analyst")
        self.assertEqual(summary["source"], "superset")

    def test_long_running_flag_uses_threshold(self):
        self.assertFalse(summarise_query(raw_query(elapsed="10.00s"), 4096, 300)["long_running"])
        self.assertTrue(summarise_query(raw_query(elapsed="6.00m"), 4096, 300)["long_running"])

    def test_sql_is_truncated_and_flagged(self):
        summary = summarise_query(raw_query(sql="S" * 10000), 100, 300)
        self.assertTrue(summary["query_truncated"])
        self.assertEqual(len(summary["query_preview"].encode("utf-8")), 100)

    def test_malformed_duration_does_not_raise(self):
        raw = raw_query()
        raw["queryStats"]["elapsedTime"] = "not-a-duration"
        summary = summarise_query(raw, 4096, 300)
        self.assertIsNone(summary["elapsed_ms"])
        self.assertFalse(summary["long_running"])

    def test_missing_stats_does_not_raise(self):
        summary = summarise_query({"queryId": "q", "state": "RUNNING"}, 4096, 300)
        self.assertEqual(summary["query_id"], "q")
        self.assertIsNone(summary["elapsed_ms"])


class H09CrossCheckTest(unittest.TestCase):
    """An empty list is ambiguous. Resolve it against JMX, never assume idle."""

    def test_empty_list_with_running_queries_is_flagged(self):
        poller = make_poller(FakeClient(queries=[]))
        poller.poll_queries(jmx_running_queries=7)  # first sighting
        snapshot = poller.poll_queries(jmx_running_queries=7)  # it persisted
        self.assertFalse(snapshot.trustworthy)
        self.assertIn("filtered", snapshot.collection_error)
        self.assertIn("rules.json", snapshot.advice)

    def test_a_single_suspicious_poll_is_not_enough(self):
        """The list and the JMX read are not atomic. A query ending between the
        two looks identical to filtering - measured live, three false trips in
        three minutes. Only a condition that persists is believed."""
        poller = make_poller(FakeClient(queries=[]))
        snapshot = poller.poll_queries(jmx_running_queries=7)
        self.assertTrue(snapshot.trustworthy, snapshot.collection_error)

    def test_the_streak_resets_when_the_cluster_looks_normal(self):
        """Two isolated races must not add up to a false alarm."""
        poller = make_poller(FakeClient(queries=[]))
        poller.poll_queries(jmx_running_queries=7)
        poller.poll_queries(jmx_running_queries=0)  # normal poll clears it
        snapshot = poller.poll_queries(jmx_running_queries=7)
        self.assertTrue(snapshot.trustworthy, snapshot.collection_error)

    def test_empty_list_with_no_running_queries_is_trusted(self):
        poller = make_poller(FakeClient(queries=[]))
        snapshot = poller.poll_queries(jmx_running_queries=0)
        self.assertTrue(snapshot.trustworthy)
        self.assertEqual(snapshot.payload["summary"]["total"], 0)

    def test_empty_list_without_jmx_data_is_not_flagged(self):
        """No cross-check available - do not invent a failure."""
        poller = make_poller(FakeClient(queries=[]))
        self.assertTrue(poller.poll_queries(jmx_running_queries=None).trustworthy)

    def test_non_empty_list_is_trusted(self):
        poller = make_poller(FakeClient(queries=[raw_query()]))
        self.assertTrue(poller.poll_queries(jmx_running_queries=1).trustworthy)

    def test_flagged_poll_does_not_count_as_success(self):
        """Otherwise the freshness metric would hide a permission outage.

        The first suspicious poll is still a success - nothing is wrong yet as
        far as we know. What must not happen is freshness continuing to advance
        once the condition is confirmed.
        """
        clock = FakeClock()
        poller = make_poller(FakeClient(queries=[]), clock=clock)
        poller.poll_queries(jmx_running_queries=7)
        first = poller.last_success[KIND_QUERIES]
        self.assertIsNotNone(first)

        clock.advance(60)
        poller.poll_queries(jmx_running_queries=7)  # confirmed - now flagged
        self.assertEqual(poller.last_success[KIND_QUERIES], first)

        clock.advance(60)
        poller.poll_queries(jmx_running_queries=7)  # stays flagged
        self.assertEqual(poller.last_success[KIND_QUERIES], first)

    def test_cross_check_uses_this_tick_s_jmx_reading(self):
        """The JMX poll in the same tick must be what the query poll is judged
        against - that is the whole point of polling JMX first."""
        client = FakeClient(queries=[], mbeans={QUERY_MANAGER: {"RunningQueries": 9}})
        repository = InMemorySnapshotRepository()
        poller = make_poller(client, repository=repository, clock=FakeClock())
        poller.tick()
        poller._next_due = {k: 0.0 for k in poller._next_due}  # make everything due again
        snapshot = [s for s in poller.tick() if s.kind == KIND_QUERIES][0]
        self.assertFalse(snapshot.trustworthy)
        self.assertIn("filtered", snapshot.collection_error)

    def test_a_stale_stored_reading_is_not_what_gets_used(self):
        """A leftover snapshot claiming running queries must not, on its own,
        condemn a fresh empty list - the current JMX poll overrides it."""
        repository = InMemorySnapshotRepository()
        repository.save(
            Snapshot(
                cluster="prod-a",
                kind=KIND_JMX,
                collected_at=utcnow(),
                payload={"mbeans": {QUERY_MANAGER: {"RunningQueries": 9}}},
            )
        )
        # This tick's JMX read says the cluster is idle, and it wins.
        client = FakeClient(queries=[], mbeans={QUERY_MANAGER: {"RunningQueries": 0}})
        poller = make_poller(client, repository=repository, clock=FakeClock())
        snapshot = [s for s in poller.tick() if s.kind == KIND_QUERIES][0]
        self.assertTrue(snapshot.trustworthy, snapshot.collection_error)

    def test_jmx_is_polled_before_queries(self):
        """Order is load-bearing: it bounds the skew the cross-check sees."""
        poller = make_poller(FakeClient(queries=[]))
        due = poller.due_kinds()
        self.assertLess(due.index(KIND_JMX), due.index(KIND_QUERIES))

    def test_stale_jmx_snapshot_does_not_trip_the_cross_check(self):
        """Observed live: a first tick after restart raised H-09 on an idle cluster.

        Queries are polled before JMX, so the stored JMX snapshot on that tick
        was hours old and still said RunningQueries=1. A false "silent
        filtering" alarm discredits the alarm.
        """
        now = utcnow()
        repository = InMemorySnapshotRepository()
        repository.save(
            Snapshot(
                cluster="prod-a",
                kind=KIND_JMX,
                collected_at=now - timedelta(hours=4),
                payload={"mbeans": {QUERY_MANAGER: {"RunningQueries": 1}}},
            )
        )
        poller = make_poller(
            FakeClient(queries=[]),
            repository=repository,
            wall_clock=lambda: now,
        )
        snapshot = poller.poll_queries(poller._last_jmx_running_queries())
        self.assertTrue(snapshot.trustworthy, snapshot.collection_error)

    def _poller_with_jmx_age(self, age_seconds, now):
        repository = InMemorySnapshotRepository()
        repository.save(
            Snapshot(
                cluster="prod-a",
                kind=KIND_JMX,
                collected_at=now - timedelta(seconds=age_seconds),
                payload={"mbeans": {QUERY_MANAGER: {"RunningQueries": 1}}},
            )
        )
        return make_poller(
            FakeClient(queries=[]),
            repository=repository,
            query_interval=3.0,
            jmx_interval=15.0,
            wall_clock=lambda: now,
        )

    def test_a_contemporaneous_jmx_reading_still_trips_the_cross_check(self):
        """The age bound must not disarm H-09 on the ticks that can judge."""
        now = utcnow()
        poller = self._poller_with_jmx_age(0.5, now)
        poller.poll_queries(poller._last_jmx_running_queries())
        snapshot = poller.poll_queries(poller._last_jmx_running_queries())
        self.assertFalse(snapshot.trustworthy)
        self.assertIn("filtered", snapshot.collection_error)

    def test_a_jmx_reading_from_a_previous_tick_abstains(self):
        """Queries are polled every 3s and JMX every 15s, so most query polls
        have no contemporaneous JMX reading. Judging against a 10s-old count is
        what produced the live false alarms - abstain instead. Genuine
        filtering persists and is caught on the next JMX-bearing tick."""
        now = utcnow()
        poller = self._poller_with_jmx_age(10.0, now)
        poller.poll_queries(poller._last_jmx_running_queries())
        snapshot = poller.poll_queries(poller._last_jmx_running_queries())
        self.assertTrue(snapshot.trustworthy, snapshot.collection_error)


class FailureIsolationTest(unittest.TestCase):
    def test_client_error_becomes_an_untrusted_snapshot_not_an_exception(self):
        client = FakeClient()
        client.raise_on["list_queries"] = TrinoUnavailable("down")
        snapshot = make_poller(client).poll_queries()
        self.assertFalse(snapshot.trustworthy)
        self.assertTrue(snapshot.advice)

    def test_forbidden_carries_actionable_advice(self):
        client = FakeClient()
        client.raise_on["list_queries"] = TrinoForbidden("denied")
        snapshot = make_poller(client).poll_queries()
        self.assertIn("rules.json", snapshot.advice)

    def test_one_bad_mbean_does_not_lose_the_others(self):
        client = FakeClient(
            mbeans={
                NODE_MANAGER_MBEAN: {"ActiveNodeCount": 13},
                "java.lang:type=Memory": {"HeapMemoryUsage": {"used": 1}},
                QUERY_MANAGER: {"RunningQueries": 2},
                "trino.memory:name=ClusterMemoryManager": {},
            }
        )
        client.raise_on["java.lang:type=Memory"] = TrinoUnavailable("boom")
        snapshot = make_poller(client).poll_jmx()
        self.assertIn(NODE_MANAGER_MBEAN, snapshot.payload["mbeans"])
        self.assertIn("java.lang:type=Memory", snapshot.payload["errors"])
        # Partial data is usable but must be labelled.
        self.assertIsNotNone(snapshot.collection_error)

    def test_all_mbeans_failing_marks_the_snapshot_untrusted(self):
        client = FakeClient()
        client.raise_on["get_mbean"] = TrinoForbidden("denied")
        snapshot = make_poller(client).poll_jmx()
        self.assertFalse(snapshot.trustworthy)
        self.assertEqual(snapshot.payload["mbeans"] if "mbeans" in snapshot.payload else {}, {})

    def test_repository_failure_does_not_kill_the_loop(self):
        class ExplodingRepository(InMemorySnapshotRepository):
            def save(self, snapshot):
                raise RuntimeError("disk on fire")

        poller = make_poller(FakeClient(queries=[raw_query()]), repository=ExplodingRepository())
        produced = poller.tick()  # must not raise
        self.assertTrue(produced)

    def test_info_poll_survives_when_authorisation_is_broken(self):
        """/v1/info is PUBLIC - the last signal that a coordinator is alive."""
        client = FakeClient()
        client.raise_on["get_mbean"] = TrinoForbidden("denied")
        client.raise_on["list_queries"] = TrinoForbidden("denied")
        poller = make_poller(client)
        self.assertTrue(poller.poll_info().trustworthy)


class SchedulingTest(unittest.TestCase):
    def test_all_kinds_are_due_on_the_first_tick(self):
        poller = make_poller(FakeClient())
        self.assertEqual(
            sorted(poller.due_kinds()), sorted([KIND_QUERIES, KIND_JMX, KIND_INFO])
        )

    def test_kinds_respect_their_own_intervals(self):
        clock = FakeClock()
        poller = make_poller(
            FakeClient(), clock=clock, query_interval=5, jmx_interval=15, info_interval=30
        )
        poller.tick()
        self.assertEqual(poller.due_kinds(), [])

        clock.advance(6)
        self.assertEqual(poller.due_kinds(), [KIND_QUERIES])
        poller.tick()

        clock.advance(10)  # 16s total: queries and jmx due, info is not
        due = sorted(poller.due_kinds())
        self.assertIn(KIND_QUERIES, due)
        self.assertIn(KIND_JMX, due)
        self.assertNotIn(KIND_INFO, due)

    def test_seconds_until_next_due_is_never_negative(self):
        clock = FakeClock()
        poller = make_poller(FakeClient(), clock=clock)
        poller.tick()
        clock.advance(100)
        self.assertGreaterEqual(poller.seconds_until_next_due(), 0.0)


class AdaptiveBackoffTest(unittest.TestCase):
    def test_large_response_raises_the_interval(self):
        client = FakeClient(queries=[raw_query()], response_bytes=6_000_000)
        poller = make_poller(
            client, response_backoff_bytes=5_000_000, response_backoff_interval=10.0
        )
        self.assertEqual(poller.query_interval, 5.0)
        poller.poll_queries()
        self.assertEqual(poller.query_interval, 10.0)

    def test_interval_recovers_only_well_below_the_trigger(self):
        client = FakeClient(queries=[raw_query()], response_bytes=6_000_000)
        poller = make_poller(
            client, response_backoff_bytes=5_000_000, response_backoff_interval=10.0
        )
        poller.poll_queries()
        self.assertEqual(poller.query_interval, 10.0)

        # Just under the trigger: hysteresis keeps the slower interval so the
        # schedule does not oscillate around the threshold.
        client.response_bytes = 4_900_000
        poller.poll_queries()
        self.assertEqual(poller.query_interval, 10.0)

        client.response_bytes = 1_000_000
        poller.poll_queries()
        self.assertEqual(poller.query_interval, 5.0)


class SnapshotTest(unittest.TestCase):
    def test_stale_detection(self):
        now = utcnow()
        snapshot = Snapshot("prod-a", KIND_QUERIES, now - timedelta(seconds=45))
        self.assertTrue(snapshot.is_stale(now, 30))
        self.assertFalse(snapshot.is_stale(now, 60))

    def test_summary_counts_running_and_queued_separately(self):
        client = FakeClient(
            queries=[
                raw_query("q1", state="RUNNING"),
                raw_query("q2", state="QUEUED"),
                raw_query("q3", state="WAITING_FOR_RESOURCES"),
                raw_query("q4", state="RUNNING", elapsed="10.00m"),
            ]
        )
        summary = make_poller(client).poll_queries(1).payload["summary"]
        self.assertEqual(summary["running"], 2)
        self.assertEqual(summary["queued"], 2)
        self.assertEqual(summary["long_running"], 1)
        self.assertEqual(summary["total"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
