"""Tests for the polling-to-health bridge.

Health is evaluated in the collector, not in the API. The engine carries state
across evaluations - the cumulative OOM counter and the stabilisation counts -
so evaluating in the API would give every replica its own counters: a transition
would be recorded once per replica, and a spike would be smoothed differently on
each one. The collector already holds the advisory lock, so it is the only place
that state is coherent.
"""

import os
import sys
import unittest
from datetime import timedelta

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.collector.health_writer import HealthWriter  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    KIND_HEALTH,
    KIND_INFO,
    KIND_JMX,
    KIND_QUERIES,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.health.engine import HealthEngine  # noqa: E402
from tms.health.tests import (  # noqa: E402
    CLUSTER_MEMORY_MBEAN,
    MEMORY_MBEAN,
    NODE_MANAGER_MBEAN,
    QUERY_MANAGER_MBEAN,
)


def healthy_snapshots(repository, cluster="prod-a", age_seconds=0):
    when = utcnow() - timedelta(seconds=age_seconds)
    repository.save(
        Snapshot(cluster, KIND_INFO, when, payload={"info": {"starting": False}})
    )
    repository.save(
        Snapshot(
            cluster,
            KIND_JMX,
            when,
            payload={
                "mbeans": {
                    NODE_MANAGER_MBEAN: {
                        "ActiveNodeCount": 13,
                        "InactiveNodeCount": 0,
                        "DrainingNodeCount": 0,
                        "DrainedNodeCount": 0,
                        "ShuttingDownNodeCount": 0,
                    },
                    MEMORY_MBEAN: {"HeapMemoryUsage": {"used": 10, "max": 100}},
                    QUERY_MANAGER_MBEAN: {
                        "FailedQueries.FiveMinute.Count": 0,
                        "StartedQueries.FiveMinute.Count": 100,
                        "InternalFailures.FiveMinute.Count": 0,
                    },
                    CLUSTER_MEMORY_MBEAN: {"QueriesKilledDueToOutOfMemory": 0},
                }
            },
        )
    )
    repository.save(Snapshot(cluster, KIND_QUERIES, when, payload={"queries": []}))


def make_writer(repository, stabilization_polls=3):
    return HealthWriter(
        engine=HealthEngine(stabilization_polls=stabilization_polls),
        repository=repository,
        stale_threshold_seconds=30,
    )


class HealthWriterTest(unittest.TestCase):
    def test_writes_a_health_snapshot(self):
        repository = InMemorySnapshotRepository()
        healthy_snapshots(repository)
        make_writer(repository).evaluate("prod-a", expected_workers=12)
        stored = repository.load("prod-a", KIND_HEALTH)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.payload["rollup_state"], "GOOD")

    def test_missing_inputs_produce_unknown_not_good(self):
        repository = InMemorySnapshotRepository()
        snapshot = make_writer(repository).evaluate("prod-a", expected_workers=12)
        self.assertEqual(snapshot.payload["rollup_state"], "UNKNOWN")

    def test_one_stale_input_makes_the_whole_verdict_stale(self):
        """A page built from a fresh reading and a ten-minute-old one is not fresh."""
        repository = InMemorySnapshotRepository()
        healthy_snapshots(repository)
        repository.save(
            Snapshot(
                "prod-a",
                KIND_JMX,
                utcnow() - timedelta(seconds=600),
                payload={"mbeans": {}},
            )
        )
        snapshot = make_writer(repository).evaluate("prod-a", expected_workers=12)
        self.assertTrue(snapshot.payload["stale"])
        self.assertEqual(snapshot.payload["rollup_state"], "UNKNOWN")

    def test_transitions_are_recorded_after_stabilisation(self):
        repository = InMemorySnapshotRepository()
        writer = make_writer(repository, stabilization_polls=2)
        healthy_snapshots(repository)
        for _ in range(2):
            writer.evaluate("prod-a", expected_workers=12)
        self.assertEqual(repository.health_events, [])

        # Coordinator goes down.
        repository.save(
            Snapshot(
                "prod-a",
                KIND_INFO,
                utcnow(),
                payload={},
                collection_error="unreachable",
                advice="The coordinator is not responding.",
            )
        )
        for _ in range(2):
            writer.evaluate("prod-a", expected_workers=12)

        h01 = [e for e in repository.health_events if e["test_id"] == "H-01"]
        self.assertEqual(len(h01), 1)
        self.assertEqual(h01[0]["to_state"], "BAD")
        self.assertTrue(h01[0]["advice"], "an event without a remedy is not actionable")

    def test_event_storage_failure_does_not_stop_the_snapshot(self):
        class ExplodingRepository(InMemorySnapshotRepository):
            def record_health_events(self, events):
                raise RuntimeError("db gone")

        repository = ExplodingRepository()
        writer = make_writer(repository, stabilization_polls=1)
        healthy_snapshots(repository)
        writer.evaluate("prod-a", expected_workers=12)
        repository.save(
            Snapshot("prod-a", KIND_INFO, utcnow(), payload={}, collection_error="down")
        )
        snapshot = writer.evaluate("prod-a", expected_workers=12)  # must not raise
        self.assertIsNotNone(snapshot)

    def test_overrides_are_applied(self):
        repository = InMemorySnapshotRepository()
        healthy_snapshots(repository)
        writer = make_writer(repository)
        snapshot = writer.evaluate(
            "prod-a", expected_workers=12, overrides={"H-05": {"enabled": False}}
        )
        test_ids = [t["id"] for t in snapshot.payload["tests"]]
        self.assertNotIn("H-05", test_ids)

    def test_oom_counter_survives_across_evaluations(self):
        """Proof that engine state must live in one process."""
        repository = InMemorySnapshotRepository()
        healthy_snapshots(repository)
        writer = make_writer(repository)
        writer.evaluate("prod-a", expected_workers=12)  # baseline 0

        jmx = repository.load("prod-a", KIND_JMX)
        jmx.payload["mbeans"][CLUSTER_MEMORY_MBEAN] = {
            "QueriesKilledDueToOutOfMemory": 4
        }
        repository.save(jmx)

        snapshot = writer.evaluate("prod-a", expected_workers=12)
        h07 = [t for t in snapshot.payload["tests"] if t["id"] == "H-07"][0]
        self.assertEqual(h07["observed_value"]["delta"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
