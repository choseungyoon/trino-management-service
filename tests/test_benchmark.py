"""The benchmark harness's rules (FR-BM-01/03/04).

FR-BM-04 gets most of the file, because it is the one requirement the document
calls non-negotiable and the one whose failure mode is silent: a run that
happened while the cluster was still in rotation produces numbers that look
exactly like good numbers.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.api.errors import Forbidden, InvalidRequest, NotFound  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.bench import guard as guards  # noqa: E402
from tms.bench.compare import NotComparable, compare, median, summarise_run  # noqa: E402
from tms.bench.queryset import QuerySetError, build_query_sets  # noqa: E402
from tms.bench.runner import BenchmarkRunner, measurements  # noqa: E402
from tms.bench.store import ActiveRunExists, InMemoryBenchmarkRepository  # noqa: E402
from tms.clients.errors import TrinoClientError  # noqa: E402
from tms.clients.sql import QueryFailed  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    GATEWAY_SCOPE,
    KIND_GATEWAY,
    KIND_QUERIES,
    Snapshot,
    utcnow,
)

ADMIN = Principal("admin1", ["admin"])
VIEWER = Principal("viewer1", ["viewer"])

SET = {"smoke": {"title": "Smoke", "queries": [
    {"name": "a", "sql": "SELECT 1"},
    {"name": "b", "sql": "SELECT 2"},
]}}


# ── query sets ────────────────────────────────────────────────────────


class QuerySetTest(unittest.TestCase):
    def test_a_write_statement_is_refused(self):
        for sql in ("DELETE FROM t", "INSERT INTO t VALUES (1)", "DROP TABLE t",
                    "CREATE TABLE t AS SELECT 1", "CALL system.runtime.kill_query('x')"):
            with self.assertRaises(QuerySetError, msg=sql):
                build_query_sets({"s": {"queries": [{"name": "q", "sql": sql}]}})

    def test_a_comment_cannot_hide_the_real_first_keyword(self):
        """The allowlist reads the statement, not the first characters of it."""
        for sql in ("-- just looking\nDELETE FROM t",
                    "/* nothing to see */ DROP TABLE t"):
            with self.assertRaises(QuerySetError, msg=sql):
                build_query_sets({"s": {"queries": [{"name": "q", "sql": sql}]}})

    def test_a_second_statement_is_refused(self):
        with self.assertRaises(QuerySetError):
            build_query_sets({"s": {"queries": [
                {"name": "q", "sql": "SELECT 1; DELETE FROM t"}]}})

    def test_a_trailing_semicolon_is_allowed_and_stripped(self):
        # Trino's client protocol takes one statement and rejects the
        # semicolon, so accepting it here and removing it is the difference
        # between a working paste and a confusing parse error.
        built = build_query_sets({"s": {"queries": [{"name": "q", "sql": "SELECT 1;"}]}})
        self.assertEqual("SELECT 1", built["s"].queries[0].sql)

    def test_read_only_starts_are_accepted(self):
        for sql in ("SELECT 1", "WITH x AS (SELECT 1) SELECT * FROM x",
                    "SHOW CATALOGS", "EXPLAIN SELECT 1", "(SELECT 1)"):
            build_query_sets({"s": {"queries": [{"name": "q", "sql": sql}]}})

    def test_duplicate_query_names_are_refused(self):
        # Results are keyed by name; a duplicate would merge two queries into
        # one column of the comparison without saying so.
        with self.assertRaises(QuerySetError):
            build_query_sets({"s": {"queries": [
                {"name": "q", "sql": "SELECT 1"}, {"name": "q", "sql": "SELECT 2"}]}})


# ── FR-BM-04 ──────────────────────────────────────────────────────────


class Snapshots:
    def __init__(self):
        self.stored = {}

    def save(self, snapshot):
        self.stored[(snapshot.cluster, snapshot.kind)] = snapshot

    def load(self, scope, kind):
        return self.stored.get((scope, kind))


class Gateway:
    def __init__(self, backends, raises=False):
        self.backends = backends
        self.raises = raises

    def list_backends(self, active_only=False):
        if self.raises:
            raise RuntimeError("gateway is down")
        return self.backends


def snapshots_with(running=0, backend_cluster="prod-a", age_seconds=0):
    from datetime import timedelta

    store = Snapshots()
    now = utcnow()
    store.save(Snapshot(GATEWAY_SCOPE, KIND_GATEWAY, now, payload={
        "backends": [{"name": "be-1", "cluster": backend_cluster, "active": True}]}))
    store.save(Snapshot("prod-a", KIND_QUERIES, now - timedelta(seconds=age_seconds),
                        payload={"summary": {"running": running}}))
    return store


class ProductionProtectionTest(unittest.TestCase):
    """FR-BM-04 — 타협 불가."""

    def check(self, gateway, snapshots, stale=120.0):
        return guards.check("prod-a", gateway, snapshots, stale)

    def test_an_active_backend_refuses_the_run(self):
        result = self.check(Gateway([{"name": "be-1", "active": True}]),
                            snapshots_with())
        self.assertFalse(result.ok)
        self.assertIn(guards.STILL_ROUTED, result.refusals)

    def test_a_deactivated_backend_on_an_idle_cluster_passes(self):
        result = self.check(Gateway([{"name": "be-1", "active": False}]),
                            snapshots_with(running=0))
        self.assertTrue(result.ok, result.refusals)
        self.assertTrue(result.checked_gateway_live)

    def test_no_gateway_is_a_refusal_not_a_pass(self):
        result = self.check(None, snapshots_with())
        self.assertFalse(result.ok)
        self.assertIn(guards.NO_GATEWAY, result.refusals)

    def test_an_unreachable_gateway_is_a_refusal(self):
        """An unknown routing state is not an excluded one."""
        result = self.check(Gateway([], raises=True), snapshots_with())
        self.assertFalse(result.ok)
        self.assertIn(guards.GATEWAY_UNREACHABLE, result.refusals)
        self.assertNotIn(guards.STILL_ROUTED, result.refusals)

    def test_a_backend_the_gateway_does_not_mention_is_not_proof_of_exclusion(self):
        # TMS and the Gateway disagreeing about what exists is not the same as
        # the backend being out of rotation.
        result = self.check(Gateway([{"name": "someone-else", "active": False}]),
                            snapshots_with())
        self.assertFalse(result.ok)
        self.assertIn(guards.STILL_ROUTED, result.refusals)

    def test_an_unmapped_cluster_is_a_refusal(self):
        result = self.check(Gateway([{"name": "be-1", "active": False}]),
                            snapshots_with(backend_cluster="somewhere-else"))
        self.assertFalse(result.ok)
        self.assertIn(guards.NO_BACKEND, result.refusals)

    def test_running_queries_refuse_the_run(self):
        result = self.check(Gateway([{"name": "be-1", "active": False}]),
                            snapshots_with(running=3))
        self.assertFalse(result.ok)
        self.assertIn(guards.QUERIES_RUNNING, result.refusals)
        self.assertEqual(3, result.running_queries)

    def test_a_stale_query_view_refuses_the_run(self):
        """What TMS can see is the past, and the past does not say it is idle now."""
        result = self.check(Gateway([{"name": "be-1", "active": False}]),
                            snapshots_with(running=0, age_seconds=600))
        self.assertFalse(result.ok)
        self.assertIn(guards.STALE_QUERY_VIEW, result.refusals)

    def test_every_refusal_carries_advice_a_person_can_act_on(self):
        for code in (guards.NO_GATEWAY, guards.NO_BACKEND, guards.STILL_ROUTED,
                     guards.GATEWAY_UNREACHABLE, guards.QUERIES_RUNNING,
                     guards.NO_QUERY_VIEW, guards.STALE_QUERY_VIEW):
            self.assertIn(code, guards.ADVICE)
            self.assertGreater(len(guards.ADVICE[code]), 40, code)

    def test_the_guard_never_deactivates_anything(self):
        """⛔ The property that keeps this out of CLAUDE.md rule 5's way.

        A benchmark that could take a cluster out of rotation would be the
        independent deactivate toggle the project has decided not to build,
        with a different label on it.
        """
        import inspect

        source = inspect.getsource(guards)
        self.assertNotIn("set_active", source)


# ── running a set ─────────────────────────────────────────────────────


class FakeSql:
    """Answers `execute()` from a script keyed by statement."""

    def __init__(self, answers):
        self.answers = answers
        self.executed = []

    def execute(self, statement):
        self.executed.append(statement)
        answer = self.answers.get(statement, {})
        if isinstance(answer, Exception):
            raise answer
        return answer


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryBenchmarkRepository()
        self.sets = build_query_sets(SET)

    def run_with(self, answers, repetitions=1):
        run = self.repository.create(
            cluster="prod-a", query_set="smoke", actor="admin1", roles=["admin"],
            reason="testing", repetitions=repetitions, guard={"ok": True})
        runner = BenchmarkRunner(sql_client_factory=lambda c: FakeSql(answers),
                                 repository=self.repository, pause_seconds=0,
                                 sleep=lambda _s: None)
        runner.start(run, self.sets["smoke"], repetitions).join(5)
        return self.repository.get(run["id"])

    def test_every_query_runs_every_repetition(self):
        finished = self.run_with({"SELECT 1": {"rows": [], "query_id": "q1",
                                               "stats": {}, "elapsed_ms": 10},
                                  "SELECT 2": {"rows": [], "query_id": "q2",
                                               "stats": {}, "elapsed_ms": 20}},
                                 repetitions=3)
        self.assertEqual("SUCCEEDED", finished["state"])
        self.assertEqual(6, len(finished["results"]))
        self.assertEqual([1, 1, 2, 2, 3, 3],
                         [r["iteration"] for r in finished["results"]])

    def test_a_failure_is_recorded_and_the_run_continues(self):
        """The finding is usually the query that failed, and the eight after it."""
        finished = self.run_with({
            "SELECT 1": QueryFailed("TABLE_NOT_FOUND: nope", query_id="q1",
                                    elapsed_ms=42),
            "SELECT 2": {"rows": [], "query_id": "q2", "stats": {}, "elapsed_ms": 20},
        })
        self.assertEqual("SUCCEEDED", finished["state"])
        failed = finished["results"][0]
        self.assertEqual("FAILED", failed["state"])
        self.assertEqual("q1", failed["trino_query_id"])
        self.assertEqual(42, failed["elapsed_ms"])
        self.assertEqual("SUCCEEDED", finished["results"][1]["state"])

    def test_a_run_where_everything_failed_is_not_a_success(self):
        # Otherwise a run full of errors enters a comparison as though it were
        # data, because the loop completed.
        finished = self.run_with({
            "SELECT 1": QueryFailed("boom"), "SELECT 2": TrinoClientError("timeout")})
        self.assertEqual("FAILED", finished["state"])
        self.assertIn("Every query failed", finished["error"])

    def test_trino_stats_are_kept_and_absences_stay_absent(self):
        finished = self.run_with({
            "SELECT 1": {"rows": [], "query_id": "q1", "elapsed_ms": 11,
                         "stats": {"elapsedTimeMillis": 100, "cpuTimeMillis": 50,
                                   "processedRows": 15000}},
            "SELECT 2": {"rows": [], "query_id": "q2", "stats": {}, "elapsed_ms": 20}})
        first, second = finished["results"]
        self.assertEqual(100, first["trino_elapsed_ms"])
        self.assertEqual(15000, first["processed_rows"])
        # Not 0: nobody measured it, and a 0 would read as a measurement.
        self.assertIsNone(second["trino_cpu_ms"])

    def test_measurements_never_invent_zeroes(self):
        self.assertEqual({"trino_elapsed_ms": None, "trino_cpu_ms": None,
                          "trino_queued_ms": None, "trino_planning_ms": None,
                          "processed_rows": None, "processed_bytes": None,
                          "peak_memory_bytes": None}, measurements(None))

    def test_two_runs_on_one_cluster_are_refused(self):
        self.repository.create(cluster="prod-a", query_set="smoke", actor="a",
                               roles=["admin"], reason="first", repetitions=1,
                               guard={})
        with self.assertRaises(ActiveRunExists):
            self.repository.create(cluster="prod-a", query_set="smoke", actor="b",
                                   roles=["admin"], reason="second", repetitions=1,
                                   guard={})


# ── comparison ────────────────────────────────────────────────────────


def run_of(run_id, cluster, timings, query_set="smoke", guard_ok=True,
           state="SUCCEEDED", repetitions=3):
    results = []
    for name, values in timings.items():
        for index, value in enumerate(values, start=1):
            results.append({"query_name": name, "iteration": index,
                            "state": "FAILED" if value is None else "SUCCEEDED",
                            "elapsed_ms": value if value is not None else 5,
                            "trino_cpu_ms": value,
                            "processed_rows": 100,
                            "error": None if value is not None else "boom"})
    return {"id": run_id, "cluster": cluster, "query_set": query_set,
            "state": state, "repetitions": repetitions,
            "guard": {"ok": guard_ok}, "results": results}


class ComparisonTest(unittest.TestCase):
    def test_the_median_ignores_a_cold_start_outlier(self):
        """Why `repetitions` exists at all: the first run is not like the rest."""
        summary = summarise_run(run_of(1, "a", {"q": [1000, 100, 110]}))
        self.assertEqual(110, summary["q"]["median_ms"])
        self.assertEqual(100, summary["q"]["fastest_ms"])

    def test_median_of_an_even_number_of_samples(self):
        self.assertEqual(15.0, median([10, 20]))
        self.assertIsNone(median([]))

    def test_a_small_difference_is_not_a_finding(self):
        result = compare(run_of(1, "a", {"q": [100, 100, 100]}),
                         run_of(2, "b", {"q": [102, 102, 102]}))
        self.assertEqual("same", result["rows"][0]["verdict"])

    def test_a_real_difference_is_named_with_its_direction(self):
        result = compare(run_of(1, "a", {"q": [100, 100, 100]}),
                         run_of(2, "b", {"q": [200, 200, 200]}))
        row = result["rows"][0]
        self.assertEqual("slower", row["verdict"])
        self.assertEqual(100.0, row["delta_percent"])
        self.assertEqual(1, result["summary"]["slower"])

    def test_different_query_sets_cannot_be_compared(self):
        # The same query name in two sets is not the same query.
        with self.assertRaises(NotComparable):
            compare(run_of(1, "a", {"q": [100]}),
                    run_of(2, "b", {"q": [100]}, query_set="other"))

    def test_a_run_cannot_be_compared_with_itself(self):
        with self.assertRaises(NotComparable):
            compare(run_of(1, "a", {"q": [100]}), run_of(1, "a", {"q": [100]}))

    def test_a_query_only_one_side_ran_is_flagged_not_averaged(self):
        result = compare(run_of(1, "a", {"q": [100]}),
                         run_of(2, "b", {"q": [100], "extra": [50]}))
        verdicts = {r["name"]: r["verdict"] for r in result["rows"]}
        self.assertEqual("only_candidate", verdicts["extra"])
        self.assertEqual(1, result["summary"]["unmatched"])

    def test_a_query_that_failed_on_one_side_is_not_called_the_same(self):
        result = compare(run_of(1, "a", {"q": [100, 100]}),
                         run_of(2, "b", {"q": [None, None]}))
        self.assertNotEqual("same", result["rows"][0]["verdict"])

    def test_a_quiet_run_against_a_busy_one_is_warned_about(self):
        result = compare(run_of(1, "a", {"q": [100]}, guard_ok=False),
                         run_of(2, "b", {"q": [100]}))
        self.assertTrue(any("different conditions" in w
                            for w in result["warnings"]))

    def test_two_busy_runs_compare_without_a_warning(self):
        """⛔ The mismatch is the finding, not the condition.

        These are run against serving clusters on purpose, so "both were busy"
        is a normal comparison - and a warning printed on every comparison is
        a warning on none.
        """
        result = compare(run_of(1, "a", {"q": [100]}, guard_ok=False),
                         run_of(2, "b", {"q": [100]}, guard_ok=False))
        self.assertEqual([], result["warnings"])

    def test_an_unfinished_run_is_warned_about(self):
        result = compare(run_of(1, "a", {"q": [100]}),
                         run_of(2, "b", {"q": [100]}, state="ABORTED"))
        self.assertTrue(any("ABORTED" in w for w in result["warnings"]))

    def test_different_repetition_counts_are_warned_about(self):
        result = compare(run_of(1, "a", {"q": [100]}, repetitions=1),
                         run_of(2, "b", {"q": [100]}, repetitions=5))
        self.assertTrue(any("repetition" in w for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
