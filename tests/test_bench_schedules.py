"""Benchmark schedules — what fires, when, and what stops firing.

⛔ This is the one feature in TMS that writes to a production cluster with
nobody watching. What is checked here is the part that makes that legal and
the part that makes it stop: every scheduled run carries the schedule's actor
and reason, a run that is already going is skipped rather than counted against
the schedule, and a schedule that keeps breaking switches itself off.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.api.errors import InvalidRequest  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.bench.queryset import build_query_sets  # noqa: E402
from tms.bench.schedules import (  # noqa: E402
    FAILURE_LIMIT,
    MIN_INTERVAL_MINUTES,
    InMemoryScheduleRepository,
    advance,
    validate,
)
from tms.bench.service import BenchmarkService  # noqa: E402
from tms.bench.setstore import InMemoryQuerySetRepository  # noqa: E402
from tms.bench.store import InMemoryBenchmarkRepository  # noqa: E402
from tms.collector.snapshot import InMemorySnapshotRepository  # noqa: E402
from tms.core.audit import AuditGuard, InMemoryAuditRepository  # noqa: E402
from tms.core.config import build_config  # noqa: E402

ADMIN = Principal("sre.kim", ["admin"])
NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)

SETS = {"adhoc": {"title": "Ad-hoc",
                  "queries": [{"name": "scan", "sql": "SELECT 1"}]}}


class RecordingRunner:
    """Stands in for the thing that would actually talk to Trino."""

    def __init__(self):
        self.started = []

    def start(self, run, query_set, repetitions):
        self.started.append(run)

    def abort(self, run_id):
        return None


def build(schedules=None, runner=None):
    config = build_config({
        "clusters": [{"name": name,
                      "coordinator_url": "https://{}.invalid:8443".format(name),
                      "expected_workers": 2}
                     for name in ("prod-a", "prod-b")],
        "trino": {"user": "tms-svc", "password": "pw"},
        "database": {"url": "postgresql://u:p@h:5432/d"},
        "benchmark": {"enabled": True},
    })
    audit = InMemoryAuditRepository()
    service = BenchmarkService(
        config=config, snapshots=InMemorySnapshotRepository(),
        audit_guard=AuditGuard(audit), repository=InMemoryBenchmarkRepository(),
        runner=runner or RecordingRunner(),
        query_sets=InMemoryQuerySetRepository(build_query_sets(SETS)),
        schedules=schedules)
    return service, audit


def a_schedule(store, **overrides):
    fields = dict(name="nightly", query_set="adhoc", clusters=["prod-a"],
                  repetitions=2, label=None, reason="watching the ad-hoc trend",
                  interval_minutes=1440, next_run_at=NOW - timedelta(minutes=1),
                  created_by="sre.kim")
    fields.update(overrides)
    return store.create(**fields)


class ValidationTest(unittest.TestCase):
    def test_a_reason_is_required(self):
        """⛔ Not paperwork. Nobody is present when a scheduled run executes,
        so this is the only explanation its audit record will ever carry."""
        with self.assertRaises(ValueError) as caught:
            validate("nightly", 1440, 1, ["prod-a"], "   ")
        self.assertIn("reason", str(caught.exception).lower())

    def test_an_interval_below_the_floor_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            validate("busy", MIN_INTERVAL_MINUTES - 1, 1, ["prod-a"], "why")
        # The refusal says what the floor is for, not just what it is.
        self.assertIn("capacity", str(caught.exception))

    def test_a_schedule_needs_a_cluster(self):
        with self.assertRaises(ValueError):
            validate("nightly", 1440, 1, [], "why")


class AdvanceTest(unittest.TestCase):
    def test_it_steps_from_the_scheduled_time_not_from_now(self):
        """⛔ Otherwise a daily 03:00 schedule drifts later every day by
        however long the run took."""
        scheduled = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
        ran_late = scheduled + timedelta(minutes=42)
        self.assertEqual(datetime(2026, 8, 28, 3, 0, tzinfo=timezone.utc),
                         advance(scheduled, 1440, now=ran_late))

    def test_missed_intervals_collapse_into_one(self):
        """⛔ TMS being down for a weekend must not produce a burst of catch-up
        benchmarks against a live cluster on Monday morning."""
        scheduled = datetime(2026, 8, 24, 3, 0, tzinfo=timezone.utc)
        monday = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(datetime(2026, 8, 28, 3, 0, tzinfo=timezone.utc),
                         advance(scheduled, 1440, now=monday))


class ClaimTest(unittest.TestCase):
    def test_claiming_moves_the_row_forward(self):
        """The claim and the reschedule are one act - a second ticker must not
        be able to take the same row."""
        store = InMemoryScheduleRepository()
        a_schedule(store)
        self.assertEqual(1, len(store.claim_due(NOW)))
        self.assertEqual([], store.claim_due(NOW), "claimed twice")

    def test_a_disabled_schedule_is_never_due(self):
        store = InMemoryScheduleRepository()
        row = a_schedule(store)
        store.update(row["id"], enabled=False)
        self.assertEqual([], store.claim_due(NOW))


class FiringTest(unittest.TestCase):
    def test_a_scheduled_run_carries_the_schedule_s_actor_and_reason(self):
        """⛔ Absolute rule 3 has no exception for "nobody was there"."""
        store = InMemoryScheduleRepository()
        a_schedule(store, clusters=["prod-a", "prod-b"])
        service, audit = build(schedules=store)

        outcomes = service.tick_schedules(now=NOW)
        self.assertEqual(["prod-a", "prod-b"], outcomes[0]["started"])

        for run in service.repository.runs:
            self.assertEqual("sre.kim", run["actor"])
            self.assertEqual("watching the ad-hoc trend", run["reason"])
            # Which schedule produced it, so the run list can say "not a
            # person".
            self.assertIsNotNone(run["schedule_id"])

        recorded = [r for r in audit.records
                    if r.action_type == "BENCHMARK_RUN"]
        self.assertEqual(2, len(recorded))
        self.assertTrue(all(r.reason for r in recorded))
        self.assertTrue(all(r.actor == "sre.kim" for r in recorded))

    def test_a_run_already_going_is_skipped_not_failed(self):
        """⛔ The guard doing its job must not pause the schedule. Counting a
        skip as a failure would switch it off for behaving correctly."""
        store = InMemoryScheduleRepository()
        row = a_schedule(store)
        service, _audit = build(schedules=store)

        service.start(ADMIN, "prod-a", query_set="adhoc", reason="by hand")
        outcome = service.tick_schedules(now=NOW)[0]

        self.assertEqual([], outcome["started"])
        self.assertTrue(outcome["refused"][0]["skipped"])
        after = store.get(row["id"])
        self.assertEqual(0, after["consecutive_failures"])
        self.assertTrue(after["enabled"])
        self.assertIn("skipped", after["last_outcome"])

    def test_repeated_failures_pause_the_schedule(self):
        """A broken set running every night forever is load with no reader."""
        store = InMemoryScheduleRepository()
        row = a_schedule(store, query_set="adhoc")
        service, _audit = build(schedules=store)

        # Make the run refuse for a reason that is the schedule's problem.
        def refuse(*_args, **_kwargs):
            raise InvalidRequest("the query set is broken")

        service.start = refuse

        for turn in range(FAILURE_LIMIT):
            store.update(row["id"], next_run_at=NOW - timedelta(minutes=1))
            service.tick_schedules(now=NOW)
            after = store.get(row["id"])
            self.assertEqual(turn + 1, after["consecutive_failures"])

        self.assertFalse(after["enabled"])
        # ⛔ Why it stopped, separately from `enabled`: "somebody switched this
        # off" and "this broke" are different answers on the screen.
        self.assertIn("Paused after", after["paused_reason"])

    def test_re_enabling_clears_the_failure_count(self):
        """The operator is saying they dealt with the cause. A counter that
        survived would trip again three failures later without three more."""
        store = InMemoryScheduleRepository()
        row = a_schedule(store)
        store.update(row["id"], enabled=False, consecutive_failures=FAILURE_LIMIT,
                     paused_reason="Paused after 3 failures in a row.")
        service, _audit = build(schedules=store)

        service.set_schedule_enabled(ADMIN, row["id"], enabled=True,
                                     reason="fixed the catalog name")
        after = store.get(row["id"])
        self.assertTrue(after["enabled"])
        self.assertEqual(0, after["consecutive_failures"])
        self.assertIsNone(after["paused_reason"])

    def test_a_tick_never_raises(self):
        """It runs on a background thread. A schedule that cannot start must
        not take the thread down with it."""
        class Broken:
            def claim_due(self, now=None):
                raise RuntimeError("the store is gone")

        service, _audit = build(schedules=Broken())
        self.assertEqual([], service.tick_schedules(now=NOW))


class PermissionTest(unittest.TestCase):
    def test_a_viewer_cannot_create_one(self):
        store = InMemoryScheduleRepository()
        service, _audit = build(schedules=store)
        from tms.api.errors import Forbidden

        with self.assertRaises(Forbidden):
            service.create_schedule(
                Principal("reader", ["viewer"]), name="n", query_set="adhoc",
                clusters=["prod-a"], interval_minutes=1440, reason="why")

    def test_the_section_is_off_rather_than_missing_when_020_is_absent(self):
        """⛔ 503/`available: false`, not 404. A feature that is switched off
        and one that does not exist look identical to a client otherwise."""
        service, _audit = build(schedules=None)
        listed = service.list_schedules(ADMIN)
        self.assertFalse(listed["available"])
        self.assertEqual([], listed["schedules"])

        from tms.api.errors import UpstreamUnavailable

        with self.assertRaises(UpstreamUnavailable):
            service.create_schedule(ADMIN, name="n", query_set="adhoc",
                                    clusters=["prod-a"], interval_minutes=1440,
                                    reason="why")


if __name__ == "__main__":
    unittest.main()
