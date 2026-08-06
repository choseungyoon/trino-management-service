"""Tests for the circuit breaker.

The property that matters: only *transient* failures open it. A 403 fails
identically forever, and opening the breaker on it would replace the one message
that tells the operator how to fix rules.json with a generic "unavailable".
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.clients.circuit import CLOSED, HALF_OPEN, OPEN, CircuitBreaker  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CircuitBreakerTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.breaker = CircuitBreaker(failure_threshold=3, reset_seconds=30, clock=self.clock)

    def test_starts_closed_and_allows_requests(self):
        self.assertEqual(self.breaker.state, CLOSED)
        self.assertTrue(self.breaker.allows_request())

    def test_opens_after_threshold_consecutive_transient_failures(self):
        for _ in range(2):
            self.breaker.record_failure()
        self.assertEqual(self.breaker.state, CLOSED, "opened one failure too early")
        self.breaker.record_failure()
        self.assertEqual(self.breaker.state, OPEN)
        self.assertFalse(self.breaker.allows_request())

    def test_success_resets_the_failure_run(self):
        self.breaker.record_failure()
        self.breaker.record_failure()
        self.breaker.record_success()
        self.breaker.record_failure()
        self.assertEqual(self.breaker.state, CLOSED)
        self.assertEqual(self.breaker.consecutive_failures, 1)

    def test_non_transient_failures_never_open_the_breaker(self):
        """403 / missing MBean must stay visible, not be masked as an outage."""
        for _ in range(20):
            self.breaker.record_failure(transient=False)
        self.assertEqual(self.breaker.state, CLOSED)
        self.assertTrue(self.breaker.allows_request())
        self.assertEqual(self.breaker.consecutive_failures, 0)

    def test_half_opens_after_reset_window(self):
        for _ in range(3):
            self.breaker.record_failure()
        self.assertEqual(self.breaker.state, OPEN)

        self.clock.advance(29)
        self.assertEqual(self.breaker.state, OPEN)
        self.assertFalse(self.breaker.allows_request())

        self.clock.advance(2)
        self.assertEqual(self.breaker.state, HALF_OPEN)
        self.assertTrue(self.breaker.allows_request(), "trial call must be admitted")

    def test_half_open_failure_reopens(self):
        for _ in range(3):
            self.breaker.record_failure()
        self.clock.advance(31)
        self.assertEqual(self.breaker.state, HALF_OPEN)
        self.breaker.record_failure()
        self.assertEqual(self.breaker.state, OPEN)
        self.assertFalse(self.breaker.allows_request())

    def test_half_open_success_closes(self):
        for _ in range(3):
            self.breaker.record_failure()
        self.clock.advance(31)
        self.breaker.record_success()
        self.assertEqual(self.breaker.state, CLOSED)
        self.assertEqual(self.breaker.consecutive_failures, 0)

    def test_seconds_until_retry_counts_down(self):
        for _ in range(3):
            self.breaker.record_failure()
        self.assertAlmostEqual(self.breaker.seconds_until_retry(), 30.0, places=3)
        self.clock.advance(10)
        self.assertAlmostEqual(self.breaker.seconds_until_retry(), 20.0, places=3)
        self.clock.advance(25)
        self.assertEqual(self.breaker.seconds_until_retry(), 0.0)

    def test_threshold_must_be_positive(self):
        with self.assertRaises(ValueError):
            CircuitBreaker(failure_threshold=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
