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
