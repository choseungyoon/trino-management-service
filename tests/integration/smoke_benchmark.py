"""Benchmark storage against a real PostgreSQL (FR-BM-01/03).

Things a fake cannot tell you:

* whether the append-only grants hold - `benchmark_result` must refuse UPDATE
  and DELETE to `tms_app`, because a number that can be edited after the fact
  is not a measurement;
* whether the CHECK constraints actually catch the states the code should
  never write (a failure with no error text, a finished run with no finish
  time, repetitions out of range);
* whether the one-run-per-cluster index fires;
* whether `guard` survives the round trip as JSON - it is the only thing that
  makes an old result trustworthy or worthless.

Not named ``test_*`` on purpose: `pytest tests/` must stay infrastructure-free.

Run:
    export TMS_SMOKE_DSN='postgresql://tms_admin@localhost:5433/tms_local'
    <venv>/bin/python -m unittest tests.integration.smoke_benchmark -v
"""

import os
import sys
import unittest
import uuid

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "src"),
)

DSN = os.environ.get("TMS_SMOKE_DSN")

try:
    import psycopg  # noqa: F401

    HAVE_PSYCOPG = True
except ImportError:  # pragma: no cover
    HAVE_PSYCOPG = False

from tms.bench.store import ActiveRunExists, PostgresBenchmarkRepository  # noqa: E402

GUARD = {"ok": True, "refusals": [], "running_queries": 0,
         "checked_gateway_live": True,
         "backends": [{"name": "be-1", "active": False}]}


@unittest.skipUnless(HAVE_PSYCOPG and DSN, "set TMS_SMOKE_DSN to run")
class BenchmarkStoreSmoke(unittest.TestCase):
    def setUp(self):
        self.repository = PostgresBenchmarkRepository(DSN)
        self.cluster = "smoke-" + uuid.uuid4().hex[:8]
        self.addCleanup(self._drop)
        self.run = self.repository.create(
            cluster=self.cluster, query_set="smoke", actor="smoke",
            roles=["admin"], reason="integration smoke", repetitions=2,
            guard=GUARD, label="baseline")

    def _drop(self):
        with psycopg.connect(DSN, autocommit=True) as connection:
            connection.execute(
                "DELETE FROM benchmark_result WHERE run_id IN"
                " (SELECT id FROM benchmark_run WHERE cluster = %s)", (self.cluster,))
            connection.execute("DELETE FROM benchmark_run WHERE cluster = %s",
                               (self.cluster,))

    def test_the_guard_survives_the_round_trip(self):
        stored = self.repository.get(self.run["id"])
        self.assertEqual(GUARD, stored["guard"])

    def test_two_runs_on_one_cluster_are_refused_by_the_index(self):
        with self.assertRaises(ActiveRunExists):
            self.repository.create(cluster=self.cluster, query_set="smoke",
                                   actor="smoke", roles=["admin"], reason="again",
                                   repetitions=1, guard={})

    def test_results_come_back_in_the_order_they_were_written(self):
        for iteration in (1, 2):
            self.repository.add_result(self.run["id"], {
                "query_name": "q", "iteration": iteration, "state": "SUCCEEDED",
                "trino_query_id": "q{}".format(iteration), "elapsed_ms": 100 + iteration,
                "trino_elapsed_ms": 90, "trino_cpu_ms": 40, "trino_queued_ms": 1,
                "trino_planning_ms": 5, "processed_rows": 10, "processed_bytes": 20,
                "peak_memory_bytes": 30, "error": None})
        stored = self.repository.get(self.run["id"])
        self.assertEqual([101, 102], [r["elapsed_ms"] for r in stored["results"]])

    def test_absent_trino_stats_stay_null(self):
        # A failure before planning produced no CPU time. 0 would read as a
        # measurement instead of an absence.
        self.repository.add_result(self.run["id"], {
            "query_name": "q", "iteration": 1, "state": "FAILED",
            "trino_query_id": None, "elapsed_ms": 12, "trino_elapsed_ms": None,
            "trino_cpu_ms": None, "trino_queued_ms": None, "trino_planning_ms": None,
            "processed_rows": None, "processed_bytes": None,
            "peak_memory_bytes": None, "error": "TABLE_NOT_FOUND"})
        result = self.repository.get(self.run["id"])["results"][0]
        self.assertIsNone(result["trino_cpu_ms"])
        self.assertEqual("TABLE_NOT_FOUND", result["error"])

    def test_finishing_sets_the_finish_time(self):
        self.repository.finish(self.run["id"], "SUCCEEDED")
        stored = self.repository.get(self.run["id"])
        self.assertEqual("SUCCEEDED", stored["state"])
        self.assertIsNotNone(stored["finished_at"])
        self.assertTrue(stored["is_terminal"])

    def test_an_unknown_outcome_carries_no_finish_time(self):
        """Nobody can say when it stopped, so no timestamp is claimed."""
        self.repository.finish(self.run["id"], "UNKNOWN")
        stored = self.repository.get(self.run["id"])
        self.assertEqual("UNKNOWN", stored["state"])
        self.assertIsNone(stored["finished_at"])

    def test_reconcile_marks_a_stranded_run_unknown(self):
        moved = self.repository.reconcile_orphans()
        self.assertGreaterEqual(moved, 1)
        stored = self.repository.get(self.run["id"])
        self.assertEqual("UNKNOWN", stored["state"])
        self.assertIn("restarted", stored["error"])

    # ── the constraints, checked directly ────────────────────────────

    def _refuses(self, statement, params):
        with psycopg.connect(DSN, autocommit=True) as connection:
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(statement, params)

    def test_a_failure_with_no_error_text_is_refused(self):
        self._refuses(
            "INSERT INTO benchmark_result (run_id, query_name, iteration, state,"
            " elapsed_ms) VALUES (%s, 'q', 1, 'FAILED', 10)", (self.run["id"],))

    def test_a_blank_reason_is_refused(self):
        self._refuses(
            "INSERT INTO benchmark_run (cluster, query_set, state, reason, actor)"
            " VALUES (%s, 'smoke', 'RUNNING', '   ', 'smoke')",
            (self.cluster + "-2",))

    def test_repetitions_out_of_range_are_refused(self):
        self._refuses(
            "INSERT INTO benchmark_run (cluster, query_set, state, reason, actor,"
            " repetitions) VALUES (%s, 'smoke', 'RUNNING', 'r', 'smoke', 999)",
            (self.cluster + "-3",))


@unittest.skipUnless(HAVE_PSYCOPG and DSN, "set TMS_SMOKE_DSN to run")
class AppendOnlySmoke(unittest.TestCase):
    """Measurements are evidence, at the audit log's grade.

    Checked as `tms_app`, the role tms-api connects as. Running it as the owner
    proves nothing - an owner always has full rights on its own tables.
    """

    APP_DSN = os.environ.get("TMS_SMOKE_APP_DSN")

    @unittest.skipUnless(APP_DSN, "set TMS_SMOKE_APP_DSN to the tms_app role")
    def test_the_application_role_cannot_edit_a_measurement(self):
        with psycopg.connect(self.APP_DSN, autocommit=True) as connection:
            for statement in ("UPDATE benchmark_result SET elapsed_ms = 1",
                              "DELETE FROM benchmark_result"):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege,
                                       msg=statement):
                    connection.execute(statement)


if __name__ == "__main__":
    unittest.main()
