"""Health evaluation: run the catalogue, roll up, emit transitions.

Three behaviours here exist because of how operators actually use a health page:

* Stale snapshots downgrade every test to UNKNOWN. Showing a 10-minute-old GOOD
  as current is worse than showing nothing, because it is believed.
* A transition is only recorded after the new state holds for
  `stabilization_polls` consecutive evaluations. One spike must not create an
  event, or the event log becomes unreadable and gets ignored.
* BAD and CONCERNING without advice are refused. The database enforces the same
  rule, but failing here produces a usable message instead of a constraint
  violation at 3am.

Python 3.9 compatible.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from tms.health.states import UNKNOWN, requires_advice, worst
from tms.health.tests import (
    ALL_TESTS,
    GATEWAY_TESTS,
    HealthContext,
    HealthResult,
)

log = logging.getLogger(__name__)

# Addresses the roll-up itself rather than an individual test (FR-CH-04).
ROLLUP_KEY = "*"


class ClusterHealth:
    __slots__ = ("cluster", "rollup_state", "rollup_enabled", "results", "evaluated_at", "stale")

    def __init__(
        self,
        cluster: str,
        rollup_state: str,
        rollup_enabled: bool,
        results: List[HealthResult],
        evaluated_at: datetime,
        stale: bool,
    ) -> None:
        self.cluster = cluster
        self.rollup_state = rollup_state
        self.rollup_enabled = rollup_enabled
        self.results = results
        self.evaluated_at = evaluated_at
        self.stale = stale

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rollup_state": self.rollup_state,
            "rollup_enabled": self.rollup_enabled,
            "stale": self.stale,
            "tests": [result.as_dict() for result in self.results],
        }


class HealthEngine:
    """Stateful across evaluations: remembers OOM counters and pending states."""

    def __init__(
        self,
        stabilization_polls: int = 3,
        gateway_enabled: bool = False,
    ) -> None:
        self.stabilization_polls = max(1, stabilization_polls)
        self.gateway_enabled = gateway_enabled
        # (cluster, test_id) -> confirmed state
        self._confirmed: Dict[str, str] = {}
        # (cluster, test_id) -> [candidate state, consecutive count]
        self._pending: Dict[str, List[Any]] = {}
        # cluster -> last cumulative OOM kill counter
        self._oom_counters: Dict[str, int] = {}

    @staticmethod
    def _key(cluster: str, test_id: str) -> str:
        return "{}::{}".format(cluster, test_id)

    def active_test_ids(self, overrides: Optional[Dict[str, Any]] = None) -> List[str]:
        """Tests in the catalogue for this deployment.

        Gateway tests are removed entirely when the adapter is off: a test that
        can only ever return UNKNOWN is noise.
        """
        overrides = overrides or {}
        test_ids = []
        for test_id in sorted(ALL_TESTS):
            if test_id in GATEWAY_TESTS and not self.gateway_enabled:
                continue
            override = overrides.get(test_id)
            if isinstance(override, dict) and override.get("enabled") is False:
                continue
            test_ids.append(test_id)
        return test_ids

    def evaluate(
        self,
        ctx: HealthContext,
        now: datetime,
        stale: bool = False,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> ClusterHealth:
        overrides = overrides or {}
        ctx.gateway_enabled = self.gateway_enabled
        ctx.previous_oom_kills = self._oom_counters.get(ctx.cluster_name)

        results: List[HealthResult] = []
        for test_id in self.active_test_ids(overrides):
            try:
                result = ALL_TESTS[test_id](ctx)
            except Exception:  # noqa: BLE001 - one bad test must not blank the page
                log.exception("health test %s raised for %s", test_id, ctx.cluster_name)
                result = HealthResult(
                    test_id,
                    test_id,
                    UNKNOWN,
                    advice="This health test raised an error. Check the TMS logs.",
                )
            if stale:
                # Never present an old reading as current.
                result = HealthResult(
                    result.test_id,
                    result.name,
                    UNKNOWN,
                    result.observed_value,
                    result.threshold,
                    "Collected data is stale. Check that tms-collector is running.",
                )
            self._check_advice(result)
            results.append(result)

        if ctx.current_oom_kills is not None:
            self._oom_counters[ctx.cluster_name] = ctx.current_oom_kills

        rollup_override = overrides.get(ROLLUP_KEY)
        rollup_enabled = not (
            isinstance(rollup_override, dict) and rollup_override.get("enabled") is False
        )
        rollup_state = worst([r.state for r in results]) if rollup_enabled else UNKNOWN

        return ClusterHealth(
            cluster=ctx.cluster_name,
            rollup_state=rollup_state,
            rollup_enabled=rollup_enabled,
            results=results,
            evaluated_at=now,
            stale=stale,
        )

    @staticmethod
    def _check_advice(result: HealthResult) -> None:
        if requires_advice(result.state) and not (result.advice or "").strip():
            # Loud, but not fatal: the operator still gets the state.
            log.error(
                "health test %s returned %s without advice - this is a bug",
                result.test_id,
                result.state,
            )
            result.advice = (
                "No remedy was supplied for this state (a bug). Check the test implementation."
            )

    def confirm_transitions(self, health: ClusterHealth) -> List[Dict[str, Any]]:
        """Return the transitions that have held long enough to be recorded.

        A state must repeat `stabilization_polls` times before it displaces the
        confirmed one. Without this a single spike writes an event, and an event
        log full of spikes is an event log nobody reads.
        """
        events: List[Dict[str, Any]] = []
        for result in health.results:
            key = self._key(health.cluster, result.test_id)
            confirmed = self._confirmed.get(key)

            if result.state == confirmed:
                self._pending.pop(key, None)
                continue

            pending = self._pending.get(key)
            if pending is None or pending[0] != result.state:
                self._pending[key] = [result.state, 1]
                continue

            pending[1] += 1
            if pending[1] < self.stabilization_polls:
                continue

            self._pending.pop(key, None)
            self._confirmed[key] = result.state
            if confirmed is None:
                # First observation is a baseline, not a transition.
                continue
            events.append(
                {
                    "cluster": health.cluster,
                    "test_id": result.test_id,
                    "from_state": confirmed,
                    "to_state": result.state,
                    "observed_value": result.observed_value,
                    "threshold": result.threshold,
                    "advice": result.advice,
                }
            )
        return events

    def confirmed_state(self, cluster: str, test_id: str) -> Optional[str]:
        return self._confirmed.get(self._key(cluster, test_id))
