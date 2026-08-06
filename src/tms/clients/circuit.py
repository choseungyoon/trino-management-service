"""A small circuit breaker for outbound calls.

Only *transient* failures count. A 403 or a missing MBean is a configuration
problem: it will fail identically forever, and opening the breaker on it would
replace an actionable message ("fix rules.json") with a generic "unavailable".

States: CLOSED -> OPEN after `failure_threshold` consecutive transient
failures; OPEN -> HALF_OPEN once `reset_seconds` have passed; HALF_OPEN admits
a single trial call which either closes the breaker or re-opens it.

Python 3.9 compatible. Not thread-safe by design: the collector owns one
breaker per cluster and drives it from a single loop.
"""

import time
from typing import Callable, Optional

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        reset_seconds: float = 30.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self._clock = clock or time.monotonic
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._state = CLOSED

    @property
    def state(self) -> str:
        # Re-evaluate lazily so callers do not need a background timer.
        if self._state == OPEN and self._clock() - self._opened_at >= self.reset_seconds:
            self._state = HALF_OPEN
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def allows_request(self) -> bool:
        """True when a call may proceed. HALF_OPEN admits exactly one trial."""
        return self.state != OPEN

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = CLOSED

    def record_failure(self, transient: bool = True) -> None:
        """Count a failure. Non-transient failures never open the breaker."""
        if not transient:
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._state = OPEN
            self._opened_at = self._clock()

    def seconds_until_retry(self) -> float:
        if self.state != OPEN:
            return 0.0
        remaining = self.reset_seconds - (self._clock() - self._opened_at)
        return max(0.0, remaining)
