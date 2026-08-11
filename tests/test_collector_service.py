"""Tests for the collector service loop.

What matters here is the failure policy: an unreachable coordinator is a normal
operating condition and must not stop the service, while a second collector
instance must stop immediately. Getting the second one wrong doubles the load on
every coordinator without anyone noticing (ARCHITECTURE.md principle A3).
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.collector.main import MIN_TICK_SLEEP_SECONDS, CollectorService  # noqa: E402
from tms.collector.snapshot import InMemorySnapshotRepository  # noqa: E402


class FakePoller:
    def __init__(self, name, next_due=5.0, explode=False):
        self.cluster_name = name
        self.ticks = 0
        self._next_due = next_due
        self.explode = explode

    def tick(self):
        self.ticks += 1
        if self.explode:
            raise RuntimeError("coordinator on fire")
        return []

    def seconds_until_next_due(self):
        return self._next_due


class ServiceLoopTest(unittest.TestCase):
    def _service(self, pollers):
        return CollectorService(config=None, repository=InMemorySnapshotRepository(), pollers=pollers)

    def test_one_failing_cluster_does_not_stop_the_others(self):
        broken = FakePoller("prod-a", explode=True)
        healthy = FakePoller("prod-b")
        service = self._service([broken, healthy])

        # Stop after a single pass so the loop terminates.
        original = healthy.tick

        def tick_then_stop():
            result = original()
            service.request_stop()
            return result

        healthy.tick = tick_then_stop

        self.assertEqual(service.run(), 0)
        self.assertEqual(broken.ticks, 1)
        self.assertEqual(healthy.ticks, 1, "healthy cluster was skipped")

    def test_stop_request_ends_the_loop(self):
        poller = FakePoller("prod-a")
        service = self._service([poller])
        service.request_stop()
        self.assertEqual(service.run(), 0)
        self.assertEqual(poller.ticks, 0, "loop ran after stop was requested")

    def test_sleep_uses_the_earliest_due_poller(self):
        service = self._service([FakePoller("a", next_due=9.0), FakePoller("b", next_due=2.0)])
        self.assertEqual(service._sleep_for(), 2.0)

    def test_sleep_never_busy_spins(self):
        """A zero interval must not turn the loop into a CPU burner."""
        service = self._service([FakePoller("a", next_due=0.0)])
        self.assertGreaterEqual(service._sleep_for(), MIN_TICK_SLEEP_SECONDS)

    def test_no_pollers_still_sleeps(self):
        self.assertGreater(self._service([])._sleep_for(), 0.0)


class SingletonLockTest(unittest.TestCase):
    """The advisory lock is what actually enforces the single-instance rule.

    The systemd unit carries a comment asking for it; this makes the second
    process exit instead.
    """

    def test_lock_key_is_stable(self):
        from tms.collector.postgres import COLLECTOR_ADVISORY_LOCK_KEY

        # Changing this silently would let two collectors coexist across an
        # upgrade, each holding a different key.
        self.assertEqual(COLLECTOR_ADVISORY_LOCK_KEY, 0x746D7301)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class RecordingHealthWriter:
    """Captures what the collector actually hands the health engine."""

    def __init__(self):
        self.calls = []

    def evaluate(self, **kwargs):
        self.calls.append(kwargs)


class GatewayHealthWiringTest(unittest.TestCase):
    """H-08 needs the Gateway backend list, and the collector is the only thing
    that can supply it.

    Written after H-08 sat at UNKNOWN ("Could not read Gateway backend list")
    on a correctly configured production cluster. The Gateway snapshot was
    being collected fine - the restart sequence used it to find the backend -
    but the health call never passed it, so the test could not have said
    anything else. 570 unit tests passed throughout: every one of them tested
    the health engine with a list handed to it directly.
    """

    def _service(self, snapshot=None, gateway_poller=object()):
        from tms.collector.snapshot import GATEWAY_SCOPE, KIND_GATEWAY, Snapshot, utcnow

        repository = InMemorySnapshotRepository()
        if snapshot is not None:
            repository.save(snapshot)
        writer = RecordingHealthWriter()
        service = CollectorService(
            config=None, repository=repository, pollers=[],
            health_writer=writer, gateway_poller=gateway_poller)
        return service, writer, repository

    @staticmethod
    def _snapshot(backends, error=None):
        from tms.collector.snapshot import GATEWAY_SCOPE, KIND_GATEWAY, Snapshot, utcnow

        return Snapshot(GATEWAY_SCOPE, KIND_GATEWAY, utcnow(),
                        payload={"backends": backends}, collection_error=error)

    def test_the_backend_list_reaches_the_health_evaluation(self):
        backends = [{"name": "trino-prod-a-1", "cluster": "prod-a", "active": True}]
        service, writer, _repo = self._service(self._snapshot(backends))
        service._evaluate_health("prod-a")
        self.assertEqual(backends, writer.calls[0]["gateway_backends"])

    def test_no_gateway_poller_means_none_not_an_empty_list(self):
        """Gateway off: H-08 is removed from the catalogue entirely. Passing []
        would instead report every cluster as unregistered."""
        service, writer, _repo = self._service(gateway_poller=None)
        service._evaluate_health("prod-a")
        self.assertIsNone(writer.calls[0]["gateway_backends"])

    def test_a_failed_gateway_read_is_none_not_an_empty_list(self):
        """None -> UNKNOWN ("could not read"). [] -> BAD ("not registered").
        A read failure must not raise a false alarm about routing."""
        service, writer, _repo = self._service(
            self._snapshot([], error="gateway refused the request"))
        service._evaluate_health("prod-a")
        self.assertIsNone(writer.calls[0]["gateway_backends"])

    def test_a_missing_snapshot_is_none(self):
        service, writer, _repo = self._service(snapshot=None)
        service._evaluate_health("prod-a")
        self.assertIsNone(writer.calls[0]["gateway_backends"])
