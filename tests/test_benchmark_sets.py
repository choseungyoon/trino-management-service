"""Query sets edited from the console (FR-BM-06).

The feature moved SQL out of a git-reviewed file and into a text box, so most
of this file is about the two things that used to be free and now are not:
the allowlist has to hold on the write path, and a set that can change has to
stop a comparison from quietly spanning the change.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)

from tms.api.errors import Forbidden, InvalidRequest, NotFound  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.bench.compare import compare  # noqa: E402
from tms.bench.queryset import build_query_sets  # noqa: E402
from tms.bench.runner import BenchmarkRunner  # noqa: E402
from tms.bench.service import BenchmarkService  # noqa: E402
from tms.bench.setstore import InMemoryQuerySetRepository  # noqa: E402
from tms.bench.store import InMemoryBenchmarkRepository  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    GATEWAY_SCOPE,
    KIND_GATEWAY,
    KIND_QUERIES,
    Snapshot,
    utcnow,
)
from tms.core.audit import ACTION_BENCHMARK_QUERY_CHANGE  # noqa: E402

from test_web_routes import build_service  # noqa: E402

ADMIN = Principal("admin1", ["admin"])
VIEWER = Principal("viewer1", ["viewer"])

SETS = {"smoke": {"title": "Smoke", "queries": [
    {"name": "a", "sql": "SELECT 1"},
    {"name": "b", "sql": "SELECT 2"},
]}}


class Gateway:
    """The backend is deactivated, which is the only state the guard allows."""

    @staticmethod
    def list_backends(active_only=False):
        return [{"name": "trino-prod-a-1", "active": False}]


class FakeSql:
    def __init__(self):
        self.executed = []

    def execute(self, statement):
        self.executed.append(statement)
        return {"rows": [], "query_id": "q", "stats": {}, "elapsed_ms": 10}


def wire():
    config, service, _trino = build_service(roles=("admin",))
    now = utcnow()
    service.repository.save(Snapshot(GATEWAY_SCOPE, KIND_GATEWAY, now, payload={
        "backends": [{"name": "trino-prod-a-1", "cluster": "prod-a",
                      "active": False}]}))
    service.repository.save(Snapshot("prod-a", KIND_QUERIES, now, payload={
        "summary": {"running": 0, "queued": 0, "total": 0}, "queries": []}))
    repository = InMemoryBenchmarkRepository()
    sql = FakeSql()
    benchmark = BenchmarkService(
        config=config, snapshots=service.repository, audit_guard=service.audit,
        repository=repository,
        runner=BenchmarkRunner(sql_client_factory=lambda c: sql,
                               repository=repository, pause_seconds=0,
                               sleep=lambda _s: None),
        query_sets=InMemoryQuerySetRepository(build_query_sets(SETS)),
        gateway_client=Gateway())
    return service, benchmark, repository, sql


# ── the allowlist, on the write path ──────────────────────────────────


class WritePathTest(unittest.TestCase):
    def setUp(self):
        self.service, self.bench, self.repository, self.sql = wire()

    def test_a_write_statement_cannot_be_saved(self):
        """⛔ The whole risk of FR-BM-06 in one test.

        Config used to be reviewed before it merged. Nothing reviews this, so
        the refusal has to happen here or it happens N times on a cluster
        nobody is watching.
        """
        for sql in ("DELETE FROM t", "DROP TABLE t", "INSERT INTO t VALUES (1)",
                    "-- harmless\nDELETE FROM t",
                    "/* nothing */ CALL system.runtime.kill_query('x')",
                    "SELECT 1; DELETE FROM t"):
            with self.assertRaises(InvalidRequest, msg=sql):
                self.bench.save_query(ADMIN, "smoke", name="bad", title="",
                                      statement=sql, reason="testing")

    def test_a_read_statement_is_saved_and_shows_up_in_the_set(self):
        self.bench.save_query(ADMIN, "smoke", name="c", title="Third",
                              statement="SELECT 3;", reason="one more query")
        found = self.bench.query_set(ADMIN, "smoke")["set"]
        names = [q["name"] for q in found["queries"]]
        self.assertIn("c", names)
        # The trailing semicolon is stripped: Trino's client protocol takes one
        # statement per request and would fail to parse it, every repetition.
        saved = [q for q in found["queries"] if q["name"] == "c"][0]
        self.assertEqual("SELECT 3", saved["sql"])

    def test_a_viewer_cannot_edit_anything(self):
        for call in (
            lambda: self.bench.save_query(VIEWER, "smoke", name="c", title="",
                                          statement="SELECT 3", reason="no"),
            lambda: self.bench.delete_query(VIEWER, "smoke", "a", reason="no"),
            lambda: self.bench.save_set(VIEWER, "other", "", "", reason="no"),
            lambda: self.bench.delete_set(VIEWER, "smoke", reason="no"),
        ):
            with self.assertRaises(Forbidden):
                call()

    def test_every_edit_is_audited_with_its_reason(self):
        self.bench.save_query(ADMIN, "smoke", name="c", title="",
                              statement="SELECT 3", reason="measuring the join")
        records = self.service.audit.repository.search()
        edits = [r for r in records
                 if r.action_type == ACTION_BENCHMARK_QUERY_CHANGE]
        self.assertEqual(1, len(edits))
        self.assertEqual("measuring the join", edits[0].reason)
        self.assertEqual("smoke.c", edits[0].target_id)

    def test_an_edit_without_a_reason_is_refused(self):
        from tms.api.errors import ReasonRequiredError

        with self.assertRaises(ReasonRequiredError):
            self.bench.save_query(ADMIN, "smoke", name="c", title="",
                                  statement="SELECT 3", reason="   ")

    def test_two_queries_cannot_share_a_name(self):
        """Results are keyed by name, so a duplicate merges two into one."""
        with self.assertRaises(InvalidRequest):
            self.bench.save_query(ADMIN, "smoke", name="a", title="",
                                  statement="SELECT 99", reason="clash")

    def test_renaming_a_query_keeps_one_row(self):
        self.bench.save_query(ADMIN, "smoke", name="a2", title="",
                              statement="SELECT 1", reason="clearer name",
                              original_name="a")
        names = [q["name"] for q in self.bench.query_set(ADMIN, "smoke")["set"]["queries"]]
        self.assertEqual(["a2", "b"], sorted(names))

    def test_a_set_key_must_be_usable_in_a_url_and_a_column(self):
        for key in ("Nightly", "night ly", "", "-lead"):
            with self.assertRaises(InvalidRequest, msg=key):
                self.bench.save_set(ADMIN, key, "", "", reason="testing")

    def test_editing_an_unknown_set_is_a_404_not_a_500(self):
        with self.assertRaises(NotFound):
            self.bench.save_query(ADMIN, "nope", name="c", title="",
                                  statement="SELECT 1", reason="testing")


# ── editing versus running ────────────────────────────────────────────


class EditWhileRunningTest(unittest.TestCase):
    def setUp(self):
        self.service, self.bench, self.repository, self.sql = wire()

    def test_a_set_being_executed_cannot_be_edited(self):
        """The runner read the statements at start.

        An edit now would not change what executes - it would change what the
        set claims executed, which is the worse of the two.
        """
        self.repository.create(cluster="prod-a", query_set="smoke",
                               actor="admin1", roles=["admin"], reason="running",
                               repetitions=1, guard={"ok": True}, queries=[])
        for call in (
            lambda: self.bench.save_query(ADMIN, "smoke", name="c", title="",
                                          statement="SELECT 3", reason="no"),
            lambda: self.bench.delete_query(ADMIN, "smoke", "a", reason="no"),
            lambda: self.bench.delete_set(ADMIN, "smoke", reason="no"),
        ):
            with self.assertRaises(InvalidRequest) as caught:
                call()
            self.assertIn("prod-a", str(caught.exception))

    def test_deleting_a_set_leaves_its_past_runs_alone(self):
        run = self.bench.start(ADMIN, "prod-a", query_set="smoke",
                               reason="baseline", repetitions=1)
        self.repository.finish(run["id"], "SUCCEEDED")
        self.bench.delete_set(ADMIN, "smoke", reason="superseded")

        self.assertIsNone(self.bench.query_sets.get("smoke"))
        kept = self.bench.run(ADMIN, run["id"])
        self.assertEqual("smoke", kept["query_set"])
        self.assertEqual(["a", "b"], [q["name"] for q in kept["queries"]])


# ── the snapshot: what actually ran ───────────────────────────────────


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.service, self.bench, self.repository, self.sql = wire()

    def _finished_run(self):
        run = self.bench.start(ADMIN, "prod-a", query_set="smoke",
                               reason="baseline", repetitions=1)
        for _ in range(50):
            found = self.repository.get(run["id"])
            if found["state"] != "RUNNING":
                return found
            import time

            time.sleep(0.01)
        self.fail("the run never finished")

    def test_a_run_records_the_statements_it_used(self):
        finished = self._finished_run()
        self.assertEqual({"a": "SELECT 1", "b": "SELECT 2"},
                         {q["name"]: q["sql"] for q in finished["queries"]})

    def test_editing_a_query_does_not_rewrite_a_finished_run(self):
        first = self._finished_run()
        self.bench.save_query(ADMIN, "smoke", name="a", title="",
                              statement="SELECT 1 + 1", reason="different shape",
                              original_name="a")
        again = self.bench.run(ADMIN, first["id"])
        self.assertEqual("SELECT 1", {q["name"]: q["sql"]
                                      for q in again["queries"]}["a"])

    def test_a_comparison_across_an_edit_says_so(self):
        """⛔ Without this the table shows a confident percentage for two
        different statements and calls one of them a regression."""
        baseline = self._finished_run()
        self.bench.save_query(ADMIN, "smoke", name="a", title="",
                              statement="SELECT 1 + 1", reason="different shape",
                              original_name="a")
        candidate = self._finished_run()

        result = compare(self.bench.run(ADMIN, baseline["id"]),
                         self.bench.run(ADMIN, candidate["id"]))
        changed = [r for r in result["rows"] if r["statement_changed"]]
        self.assertEqual(["a"], [r["name"] for r in changed])
        self.assertTrue(any("statement changed" in w for w in result["warnings"]))
        # `b` did not change, so it must not be flagged - a warning on every
        # row is a warning on none.
        self.assertFalse([r for r in result["rows"]
                          if r["name"] == "b" and r["statement_changed"]])

    def test_runs_without_a_snapshot_are_not_guessed_at(self):
        """Runs from before 018 kept no statements. "Cannot tell" is the answer."""
        old = self.repository.create(
            cluster="prod-a", query_set="smoke", actor="admin1", roles=["admin"],
            reason="before 018", repetitions=1, guard={"ok": True}, queries=[])
        self.repository.finish(old["id"], "SUCCEEDED")
        new = self._finished_run()
        result = compare(self.bench.run(ADMIN, old["id"]),
                         self.bench.run(ADMIN, new["id"]))
        self.assertFalse(any(r["statement_changed"] for r in result["rows"]))


# ── per-query history ─────────────────────────────────────────────────


class QueryHistoryTest(unittest.TestCase):
    def setUp(self):
        self.service, self.bench, self.repository, self.sql = wire()

    def _run_once(self):
        run = self.bench.start(ADMIN, "prod-a", query_set="smoke",
                               reason="measuring", repetitions=2)
        for _ in range(50):
            if self.repository.get(run["id"])["state"] != "RUNNING":
                return run
            import time

            time.sleep(0.01)
        self.fail("the run never finished")

    def test_every_execution_appears_once(self):
        self._run_once()
        self._run_once()
        history = self.bench.query_history(ADMIN, "smoke", "a")
        # Two runs, two repetitions each. Not folded to a median: the question
        # this page answers is "when did it change", and a median hides it.
        self.assertEqual(4, len(history["history"]))
        self.assertFalse(history["changed"])

    def test_executions_taken_before_an_edit_are_marked(self):
        self._run_once()
        self.bench.save_query(ADMIN, "smoke", name="a", title="",
                              statement="SELECT 1 + 1", reason="different shape",
                              original_name="a")
        self._run_once()
        history = self.bench.query_history(ADMIN, "smoke", "a")
        self.assertTrue(history["changed"])
        self.assertEqual(2, sum(1 for r in history["history"] if r["differs"]))

    def test_an_unknown_query_is_a_404(self):
        with self.assertRaises(NotFound):
            self.bench.query_history(ADMIN, "smoke", "nope")


# ── the second check, at execution time ───────────────────────────────


class RunnerRefusalTest(unittest.TestCase):
    def test_a_statement_changed_underneath_is_refused_before_execution(self):
        """The row can reach the table through psql, not only through the form.

        This is the last gate before N executions, and it costs a regex.
        """
        service, bench, repository, sql = wire()
        smuggled = bench.query_sets.get("smoke")
        smuggled.queries[0].sql = "DELETE FROM t"

        run = repository.create(cluster="prod-a", query_set="smoke",
                                actor="admin1", roles=["admin"], reason="testing",
                                repetitions=1, guard={"ok": True}, queries=[])
        runner = BenchmarkRunner(sql_client_factory=lambda c: sql,
                                 repository=repository, pause_seconds=0,
                                 sleep=lambda _s: None)
        runner.start(run, smuggled, 1).join(5)

        self.assertNotIn("DELETE FROM t", sql.executed)
        finished = repository.get(run["id"])
        refused = [r for r in finished["results"] if r["query_name"] == "a"]
        self.assertEqual("FAILED", refused[0]["state"])
        self.assertIn("Refused before execution", refused[0]["error"])


if __name__ == "__main__":
    unittest.main()
