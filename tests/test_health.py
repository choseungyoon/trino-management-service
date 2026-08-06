"""Tests for the health catalogue and engine.

The cases worth protecting, in order of how badly getting them wrong would hurt:

* H-03 must not call a planned drain a failure. If the health page turns red
  every time someone shrinks the fleet on purpose, operators stop reading it -
  and then it is useless during a real incident.
* UNKNOWN must outrank GOOD in the roll-up. Missing data rendered as green is
  the failure mode this whole design exists to avoid.
* H-07 must judge the delta. The OOM counter is cumulative, so reading it
  absolutely leaves a cluster BAD forever after its first OOM.
* Stale snapshots must downgrade everything. An old GOOD shown as current is
  worse than showing nothing, because it is believed.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.collector.snapshot import KIND_INFO, KIND_JMX, KIND_QUERIES, Snapshot  # noqa: E402
from tms.health.engine import ROLLUP_KEY, HealthEngine  # noqa: E402
from tms.health.states import BAD, CONCERNING, GOOD, UNKNOWN, worst  # noqa: E402
from tms.health.tests import (  # noqa: E402
    ALL_TESTS,
    CLUSTER_MEMORY_MBEAN,
    MEMORY_MBEAN,
    NODE_MANAGER_MBEAN,
    QUERY_MANAGER_MBEAN,
    HealthContext,
    h01_coordinator_responsive,
    h02_startup_complete,
    h03_worker_registration,
    h04_heap_usage,
    h05_query_failure_rate,
    h06_internal_failures,
    h07_oom_kills,
    h09_permission_self_check,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def jmx_snapshot(**mbeans):
    return Snapshot("prod-a", KIND_JMX, NOW, payload={"mbeans": mbeans})


def info_snapshot(starting=False, ok=True):
    if not ok:
        return Snapshot(
            "prod-a", KIND_INFO, NOW, payload={}, collection_error="unreachable",
            advice="The coordinator is not responding.",
        )
    return Snapshot("prod-a", KIND_INFO, NOW, payload={"info": {"starting": starting}})


def node_mbean(active=13, draining=0, drained=0, shutting_down=0, inactive=0):
    return {
        "ActiveNodeCount": active,
        "InactiveNodeCount": inactive,
        "DrainingNodeCount": draining,
        "DrainedNodeCount": drained,
        "ShuttingDownNodeCount": shutting_down,
    }


def context(**kwargs):
    kwargs.setdefault("cluster_name", "prod-a")
    kwargs.setdefault("expected_workers", 12)
    kwargs.setdefault("thresholds", {})
    return HealthContext(**kwargs)


class StatesTest(unittest.TestCase):
    def test_unknown_outranks_good(self):
        """Missing data must never render as healthy."""
        self.assertEqual(worst([GOOD, UNKNOWN]), UNKNOWN)
        self.assertEqual(worst([GOOD, GOOD]), GOOD)

    def test_bad_outranks_everything(self):
        self.assertEqual(worst([GOOD, UNKNOWN, CONCERNING, BAD]), BAD)

    def test_empty_rolls_up_to_unknown(self):
        """All tests disabled means we do not know, not that all is well."""
        self.assertEqual(worst([]), UNKNOWN)


class H01H02Test(unittest.TestCase):
    def test_responsive_coordinator_is_good(self):
        self.assertEqual(h01_coordinator_responsive(context(info=info_snapshot())).state, GOOD)

    def test_unreachable_coordinator_is_bad_with_advice(self):
        result = h01_coordinator_responsive(context(info=info_snapshot(ok=False)))
        self.assertEqual(result.state, BAD)
        self.assertTrue(result.advice)

    def test_no_data_is_unknown_not_good(self):
        self.assertEqual(h01_coordinator_responsive(context()).state, UNKNOWN)

    def test_starting_coordinator_is_concerning(self):
        result = h02_startup_complete(context(info=info_snapshot(starting=True)))
        self.assertEqual(result.state, CONCERNING)
        self.assertIn("still starting", result.advice)

    def test_started_coordinator_is_good(self):
        self.assertEqual(h02_startup_complete(context(info=info_snapshot())).state, GOOD)

    def test_missing_starting_field_is_unknown(self):
        snapshot = Snapshot("prod-a", KIND_INFO, NOW, payload={"info": {"environment": "prod"}})
        self.assertEqual(h02_startup_complete(context(info=snapshot)).state, UNKNOWN)


class H03WorkerRegistrationTest(unittest.TestCase):
    """ActiveNodeCount includes the coordinator - verified 12 workers -> 13."""

    def test_full_fleet_is_good(self):
        ctx = context(jmx=jmx_snapshot(**{NODE_MANAGER_MBEAN: node_mbean(active=13)}))
        result = h03_worker_registration(ctx)
        self.assertEqual(result.state, GOOD)
        self.assertEqual(result.observed_value["active_workers"], 12)

    def test_coordinator_not_counted_when_configured(self):
        ctx = context(
            jmx=jmx_snapshot(**{NODE_MANAGER_MBEAN: node_mbean(active=12)}),
            coordinator_counted_in_active_nodes=False,
        )
        self.assertEqual(h03_worker_registration(ctx).observed_value["active_workers"], 12)

    def test_planned_drain_is_not_reported_as_failure(self):
        """Two workers deliberately draining: still GOOD."""
        ctx = context(
            jmx=jmx_snapshot(**{NODE_MANAGER_MBEAN: node_mbean(active=11, draining=2)})
        )
        result = h03_worker_registration(ctx)
        self.assertEqual(result.state, GOOD, "a planned drain was reported as a fault")
        self.assertEqual(result.observed_value["unplanned_missing"], 0)

    def test_shutting_down_and_drained_also_count_as_planned(self):
        """The five node sets are disjoint - CoordinatorNodeManager switches on
        state, so each node lands in exactly one (verified @477). The counts
        therefore add up: 9 active workers + 1 drained + 2 shutting down = 12."""
        ctx = context(
            jmx=jmx_snapshot(
                **{NODE_MANAGER_MBEAN: node_mbean(active=10, drained=1, shutting_down=2)}
            )
        )
        result = h03_worker_registration(ctx)
        self.assertEqual(result.state, GOOD)
        self.assertEqual(result.observed_value["unplanned_missing"], 0)

    def test_counts_that_do_not_add_up_reveal_missing_nodes(self):
        """A node in GONE or INVALID state appears in none of the five counters,
        so it shows up here as unplanned - which is exactly right."""
        ctx = context(
            jmx=jmx_snapshot(
                **{NODE_MANAGER_MBEAN: node_mbean(active=9, drained=1, shutting_down=2)}
            )
        )
        result = h03_worker_registration(ctx)
        self.assertEqual(result.observed_value["unplanned_missing"], 1)
        self.assertEqual(result.state, CONCERNING)

    def test_unplanned_loss_is_reported(self):
        ctx = context(jmx=jmx_snapshot(**{NODE_MANAGER_MBEAN: node_mbean(active=11)}))
        result = h03_worker_registration(ctx)
        self.assertIn(result.state, (CONCERNING, BAD))
        self.assertEqual(result.observed_value["unplanned_missing"], 2)
        self.assertTrue(result.advice)

    def test_large_unplanned_loss_is_bad(self):
        ctx = context(jmx=jmx_snapshot(**{NODE_MANAGER_MBEAN: node_mbean(active=6)}))
        self.assertEqual(h03_worker_registration(ctx).state, BAD)

    def test_mixed_planned_and_unplanned_reports_only_the_unplanned(self):
        # 12 expected; 8 active workers; 2 draining on purpose -> 2 unplanned.
        ctx = context(
            jmx=jmx_snapshot(**{NODE_MANAGER_MBEAN: node_mbean(active=9, draining=2)})
        )
        result = h03_worker_registration(ctx)
        self.assertEqual(result.observed_value["unplanned_missing"], 2)
        self.assertIn("planned shutdown", result.advice)

    def test_missing_mbean_is_unknown_with_advice(self):
        result = h03_worker_registration(context(jmx=jmx_snapshot()))
        self.assertEqual(result.state, UNKNOWN)
        self.assertTrue(result.advice)


class H04HeapTest(unittest.TestCase):
    def _ctx(self, used, maximum=100):
        return context(
            jmx=jmx_snapshot(**{MEMORY_MBEAN: {"HeapMemoryUsage": {"used": used, "max": maximum}}})
        )

    def test_thresholds(self):
        self.assertEqual(h04_heap_usage(self._ctx(50)).state, GOOD)
        self.assertEqual(h04_heap_usage(self._ctx(85)).state, CONCERNING)
        self.assertEqual(h04_heap_usage(self._ctx(95)).state, BAD)

    def test_unreported_max_is_unknown_not_a_division_by_zero(self):
        self.assertEqual(h04_heap_usage(self._ctx(10, maximum=-1)).state, UNKNOWN)


class H05H06Test(unittest.TestCase):
    def _ctx(self, failed=0, completed=100, internal=0, started=None):
        mbean = {
            "FailedQueries.FiveMinute.Count": failed,
            "CompletedQueries.FiveMinute.Count": completed,
            "InternalFailures.FiveMinute.Count": internal,
        }
        if started is not None:
            mbean["StartedQueries.FiveMinute.Count"] = started
        return context(jmx=jmx_snapshot(**{QUERY_MANAGER_MBEAN: mbean}))

    def test_failure_rate_thresholds(self):
        self.assertEqual(h05_query_failure_rate(self._ctx(failed=1)).state, GOOD)
        self.assertEqual(h05_query_failure_rate(self._ctx(failed=10)).state, CONCERNING)
        self.assertEqual(h05_query_failure_rate(self._ctx(failed=30)).state, BAD)

    def test_rate_is_measured_against_completed_not_started(self):
        """Observed live on 477: the UI showed a 120.5% failure rate.

        A query rejected during analysis increments Failed and Completed but
        never Started, so failed/started can exceed 100% (measured: 10 such
        queries gave completed +12, failed +11, started +1). Only the
        completed-based denominator is bounded.
        """
        ctx = context(
            jmx=jmx_snapshot(
                **{
                    QUERY_MANAGER_MBEAN: {
                        "FailedQueries.FiveMinute.Count": 41.4,
                        "StartedQueries.FiveMinute.Count": 33.4,  # the old, wrong one
                        "CompletedQueries.FiveMinute.Count": 74.8,
                    }
                }
            )
        )
        result = h05_query_failure_rate(ctx)
        self.assertLessEqual(result.observed_value, 100.0)
        self.assertAlmostEqual(result.observed_value, 55.3, places=1)

    def test_impossible_ratio_is_clamped_never_rendered(self):
        """Belt and braces: a health page must not show 120%."""
        result = h05_query_failure_rate(self._ctx(failed=120, completed=100))
        self.assertEqual(result.observed_value, 100.0)

    def test_no_traffic_is_unknown_not_good(self):
        """Zero queries is not health - it may be the incident."""
        result = h05_query_failure_rate(self._ctx(failed=0, completed=0))
        self.assertEqual(result.state, UNKNOWN)
        self.assertIn("No queries completed", result.advice)

    def test_internal_failures_are_stricter_than_user_errors(self):
        """One internal failure already matters; user syntax errors do not."""
        self.assertEqual(h06_internal_failures(self._ctx(internal=0)).state, GOOD)
        self.assertEqual(h06_internal_failures(self._ctx(internal=1)).state, CONCERNING)
        self.assertEqual(h06_internal_failures(self._ctx(internal=9)).state, BAD)
        # Meanwhile 100% user-error failures leave H-05 BAD but H-06 GOOD.
        self.assertEqual(h06_internal_failures(self._ctx(failed=100, internal=0)).state, GOOD)


class H07OomTest(unittest.TestCase):
    def _ctx(self, total, previous=None):
        ctx = context(
            jmx=jmx_snapshot(**{CLUSTER_MEMORY_MBEAN: {"QueriesKilledDueToOutOfMemory": total}})
        )
        ctx.previous_oom_kills = previous
        return ctx

    def test_first_observation_establishes_a_baseline(self):
        """A cumulative counter must not make a freshly started TMS report BAD."""
        result = h07_oom_kills(self._ctx(total=500))
        self.assertEqual(result.state, GOOD)
        self.assertEqual(result.observed_value["total"], 500)

    def test_judged_on_delta_not_absolute(self):
        self.assertEqual(h07_oom_kills(self._ctx(total=500, previous=500)).state, GOOD)
        self.assertEqual(h07_oom_kills(self._ctx(total=502, previous=500)).state, CONCERNING)
        self.assertEqual(h07_oom_kills(self._ctx(total=510, previous=500)).state, BAD)

    def test_counter_reset_after_restart_is_not_a_negative_delta(self):
        result = h07_oom_kills(self._ctx(total=3, previous=500))
        self.assertEqual(result.state, GOOD)
        self.assertEqual(result.observed_value["delta"], 0)


class H09PermissionTest(unittest.TestCase):
    def test_trustworthy_snapshot_is_good(self):
        snapshot = Snapshot("prod-a", KIND_QUERIES, NOW, payload={"queries": []})
        self.assertEqual(h09_permission_self_check(context(queries=snapshot)).state, GOOD)

    def test_filtered_list_surfaces_as_unknown_with_the_collector_advice(self):
        snapshot = Snapshot(
            "prod-a",
            KIND_QUERIES,
            NOW,
            payload={},
            collection_error="query list is empty but JMX reports 7 running queries",
            advice="Check the tms-svc account's queries: view grant in rules.json.",
        )
        result = h09_permission_self_check(context(queries=snapshot))
        self.assertEqual(result.state, UNKNOWN)
        self.assertIn("rules.json", result.advice)


class EngineTest(unittest.TestCase):
    def _healthy_context(self):
        return context(
            info=info_snapshot(),
            jmx=jmx_snapshot(
                **{
                    NODE_MANAGER_MBEAN: node_mbean(active=13),
                    MEMORY_MBEAN: {"HeapMemoryUsage": {"used": 10, "max": 100}},
                    QUERY_MANAGER_MBEAN: {
                        "FailedQueries.FiveMinute.Count": 0,
                        "StartedQueries.FiveMinute.Count": 100,
                        "CompletedQueries.FiveMinute.Count": 100,
                        "InternalFailures.FiveMinute.Count": 0,
                    },
                    CLUSTER_MEMORY_MBEAN: {"QueriesKilledDueToOutOfMemory": 0},
                }
            ),
            queries=Snapshot("prod-a", KIND_QUERIES, NOW, payload={"queries": []}),
        )

    def test_healthy_cluster_rolls_up_good(self):
        engine = HealthEngine()
        health = engine.evaluate(self._healthy_context(), NOW)
        self.assertEqual(health.rollup_state, GOOD, [r.as_dict() for r in health.results])

    def test_gateway_test_absent_when_adapter_disabled(self):
        """A permanently UNKNOWN test is noise, so it is removed entirely."""
        health = HealthEngine(gateway_enabled=False).evaluate(self._healthy_context(), NOW)
        self.assertNotIn("H-08", [r.test_id for r in health.results])

    def test_gateway_test_present_when_adapter_enabled(self):
        health = HealthEngine(gateway_enabled=True).evaluate(self._healthy_context(), NOW)
        self.assertIn("H-08", [r.test_id for r in health.results])

    def test_stale_downgrades_every_test_to_unknown(self):
        health = HealthEngine().evaluate(self._healthy_context(), NOW, stale=True)
        self.assertTrue(all(r.state == UNKNOWN for r in health.results))
        self.assertEqual(health.rollup_state, UNKNOWN)
        self.assertTrue(all(r.advice for r in health.results))

    def test_disabled_test_is_excluded_from_rollup(self):
        ctx = self._healthy_context()
        ctx.jmx = jmx_snapshot(**{NODE_MANAGER_MBEAN: node_mbean(active=6)})  # H-03 -> BAD
        engine = HealthEngine()
        self.assertEqual(engine.evaluate(ctx, NOW).rollup_state, BAD)

        overrides = {"H-03": {"enabled": False}}
        health = engine.evaluate(ctx, NOW, overrides=overrides)
        self.assertNotIn("H-03", [r.test_id for r in health.results])
        self.assertNotEqual(health.rollup_state, BAD)

    def test_rollup_can_be_disabled_separately(self):
        engine = HealthEngine()
        health = engine.evaluate(
            self._healthy_context(), NOW, overrides={ROLLUP_KEY: {"enabled": False}}
        )
        self.assertFalse(health.rollup_enabled)
        self.assertTrue(health.results, "individual tests must still be evaluated")

    def test_bad_or_concerning_always_carries_advice(self):
        ctx = self._healthy_context()
        ctx.jmx = jmx_snapshot(**{NODE_MANAGER_MBEAN: node_mbean(active=6)})
        health = HealthEngine().evaluate(ctx, NOW)
        for result in health.results:
            if result.state in (BAD, CONCERNING):
                self.assertTrue(result.advice, "{} has no advice".format(result.test_id))

    def test_a_raising_test_does_not_blank_the_page(self):
        engine = HealthEngine()
        original = ALL_TESTS["H-04"]
        try:
            ALL_TESTS["H-04"] = lambda ctx: (_ for _ in ()).throw(RuntimeError("boom"))
            health = engine.evaluate(self._healthy_context(), NOW)
        finally:
            ALL_TESTS["H-04"] = original
        by_id = {r.test_id: r for r in health.results}
        self.assertEqual(by_id["H-04"].state, UNKNOWN)
        self.assertEqual(by_id["H-01"].state, GOOD, "other tests were lost")

    def test_oom_counter_is_carried_between_evaluations(self):
        engine = HealthEngine()
        ctx = self._healthy_context()
        engine.evaluate(ctx, NOW)  # baseline: 0

        ctx2 = self._healthy_context()
        ctx2.jmx = jmx_snapshot(
            **{CLUSTER_MEMORY_MBEAN: {"QueriesKilledDueToOutOfMemory": 5}}
        )
        health = engine.evaluate(ctx2, NOW)
        h07 = [r for r in health.results if r.test_id == "H-07"][0]
        self.assertEqual(h07.observed_value["delta"], 5)
        self.assertEqual(h07.state, BAD)


class StabilizationTest(unittest.TestCase):
    """One spike must not create an event; an event log of spikes is ignored."""

    def _engine_and_contexts(self):
        engine = HealthEngine(stabilization_polls=3)
        healthy = context(info=info_snapshot())
        broken = context(info=info_snapshot(ok=False))
        return engine, healthy, broken

    def _evaluate(self, engine, ctx):
        health = engine.evaluate(ctx, NOW)
        return engine.confirm_transitions(health)

    def test_first_observation_is_a_baseline_not_a_transition(self):
        engine, healthy, _ = self._engine_and_contexts()
        for _ in range(3):
            self.assertEqual(self._evaluate(engine, healthy), [])
        self.assertEqual(engine.confirmed_state("prod-a", "H-01"), GOOD)

    def test_transition_requires_consecutive_confirmations(self):
        engine, healthy, broken = self._engine_and_contexts()
        for _ in range(3):
            self._evaluate(engine, healthy)

        self.assertEqual(self._evaluate(engine, broken), [])
        self.assertEqual(self._evaluate(engine, broken), [])
        events = self._evaluate(engine, broken)
        h01 = [e for e in events if e["test_id"] == "H-01"]
        self.assertEqual(len(h01), 1)
        self.assertEqual(h01[0]["from_state"], GOOD)
        self.assertEqual(h01[0]["to_state"], BAD)
        self.assertTrue(h01[0]["advice"])

    def test_a_single_spike_produces_no_event(self):
        engine, healthy, broken = self._engine_and_contexts()
        for _ in range(3):
            self._evaluate(engine, healthy)
        self._evaluate(engine, broken)  # one bad poll
        self.assertEqual(self._evaluate(engine, healthy), [], "a spike created an event")
        self.assertEqual(engine.confirmed_state("prod-a", "H-01"), GOOD)

    def test_flapping_never_confirms(self):
        engine, healthy, broken = self._engine_and_contexts()
        for _ in range(3):
            self._evaluate(engine, healthy)
        events = []
        for _ in range(6):
            events.extend(self._evaluate(engine, broken))
            events.extend(self._evaluate(engine, healthy))
        self.assertEqual([e for e in events if e["test_id"] == "H-01"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
