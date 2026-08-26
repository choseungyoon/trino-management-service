"""The benchmark screens and the service in front of them (FR-BM-01/03/04).

The guard has its own tests. What is checked here is that the refusal actually
reaches the request: a guard that returns "no" and a service that starts anyway
would pass every test in test_benchmark.py.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)

try:
    import httpx
    from fastapi import FastAPI  # noqa: F401
    from jinja2 import Environment  # noqa: F401
    import multipart  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

from tms.api.errors import Forbidden, InvalidRequest, NotFound  # noqa: E402
from tms.api.main import create_app  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.bench.queryset import build_query_sets  # noqa: E402
from tms.bench.service import BenchmarkService  # noqa: E402
from tms.bench.store import InMemoryBenchmarkRepository  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    GATEWAY_SCOPE,
    KIND_GATEWAY,
    KIND_QUERIES,
    Snapshot,
    utcnow,
)
from tms.core.audit import ACTION_BENCHMARK_RUN  # noqa: E402

from test_web_routes import PASSWORD, build_service, client_for, sign_in  # noqa: E402

ADMIN = Principal("admin1", ["admin"])
VIEWER = Principal("viewer1", ["viewer"])

SETS = {"smoke": {"title": "Smoke", "queries": [{"name": "a", "sql": "SELECT 1"}]}}


class Gateway:
    def __init__(self, active=False, clusters=("prod-a",), refuse=None):
        self.active = active
        self.clusters = clusters
        self.refuse = refuse
        self.set_active_calls = []

    def list_backends(self, active_only=False):
        return [{"name": "trino-{}-1".format(c),
                 "active": self.active or c == self.refuse}
                for c in self.clusters]

    def set_active(self, name, active):  # pragma: no cover - must never be called
        self.set_active_calls.append((name, active))


class RecordingRunner:
    def __init__(self):
        self.started = []
        self.aborted = []

    def start(self, run, query_set, repetitions):
        self.started.append((run["id"], query_set.key, repetitions))

    def abort(self, run_id):
        self.aborted.append(run_id)


def wire(roles=("admin",), gateway_active=False, running=0, refuse_cluster=None,
         clusters=("prod-a", "prod-b")):
    config, service, _trino = build_service(roles=roles, clusters=clusters)
    now = utcnow()
    backends = [{"name": "trino-{}-1".format(cluster), "cluster": cluster,
                 "active": gateway_active or cluster == refuse_cluster}
                for cluster in config.cluster_names]
    service.repository.save(Snapshot(GATEWAY_SCOPE, KIND_GATEWAY, now,
                                     payload={"backends": backends}))
    for cluster in config.cluster_names:
        service.repository.save(Snapshot(cluster, KIND_QUERIES, now, payload={
            "summary": {"running": running, "queued": 0, "total": running},
            "queries": []}))
    repository = InMemoryBenchmarkRepository()
    runner = RecordingRunner()
    benchmark = BenchmarkService(
        config=config, snapshots=service.repository, audit_guard=service.audit,
        repository=repository, runner=runner,
        query_sets=build_query_sets(SETS),
        gateway_client=Gateway(gateway_active, tuple(config.cluster_names),
                               refuse_cluster))
    return config, service, benchmark, repository, runner


class BenchmarkServiceTest(unittest.TestCase):
    def test_a_run_starts_when_the_cluster_is_excluded_and_idle(self):
        _c, service, benchmark, repository, runner = wire()
        run = benchmark.start(ADMIN, "prod-a", query_set="smoke",
                              reason="comparing heap settings", repetitions=2)
        self.assertEqual("RUNNING", run["state"])
        self.assertEqual([(run["id"], "smoke", 2)], runner.started)
        # The guard's findings are stored with the run: six months from now
        # that column is the only way to tell this number is comparable.
        self.assertTrue(run["guard"]["ok"])

    def test_the_run_is_audited_with_its_reason(self):
        _c, service, benchmark, _r, _runner = wire()
        benchmark.start(ADMIN, "prod-a", query_set="smoke", reason="why not")
        records = service.audit.repository.search(limit=10)
        self.assertEqual(ACTION_BENCHMARK_RUN, records[0].action_type)
        self.assertEqual("why not", records[0].reason)
        self.assertEqual("prod-a", records[0].target_cluster)

    def test_a_run_without_a_reason_is_refused(self):
        from tms.api.errors import ReasonRequiredError

        _c, _s, benchmark, repository, runner = wire()
        with self.assertRaises(ReasonRequiredError):
            benchmark.start(ADMIN, "prod-a", query_set="smoke", reason="  ")
        self.assertEqual([], runner.started)

    # ── FR-BM-04, through the service ────────────────────────────────

    def test_a_cluster_still_in_rotation_runs_and_is_labelled(self):
        """Serving traffic is a caveat on the numbers, not a refusal.

        These are run on a schedule against the clusters people actually use;
        a gate would make that the one thing the feature could not do.
        """
        _c, _s, benchmark, repository, runner = wire(gateway_active=True)
        run = benchmark.start(ADMIN, "prod-a", query_set="smoke", reason="please")
        self.assertEqual(1, len(runner.started))
        self.assertFalse(run["guard"]["ok"])
        self.assertIn("still_routed", run["guard"]["refusals"])

    def test_a_busy_cluster_runs_and_is_labelled(self):
        _c, _s, benchmark, repository, runner = wire(running=4)
        run = benchmark.start(ADMIN, "prod-a", query_set="smoke", reason="please")
        self.assertEqual(1, len(runner.started))
        self.assertFalse(run["guard"]["ok"])
        self.assertIn("queries_running", run["guard"]["refusals"])

    def test_the_condition_is_stored_on_the_run_not_only_shown(self):
        """⛔ The whole safeguard now.

        Six months from now this column is the only way to tell a number taken
        on a quiet cluster from one taken while production was landing on the
        same coordinator.
        """
        _c, _s, benchmark, repository, _runner = wire(gateway_active=True)
        run = benchmark.start(ADMIN, "prod-a", query_set="smoke", reason="please")
        stored = repository.get(run["id"])
        self.assertFalse(stored["guard"]["ok"])
        self.assertTrue(stored["guard"]["advice"])

    def test_two_runs_on_one_cluster_are_still_refused(self):
        """The one refusal left. Two runs on a cluster measure each other."""
        _c, _s, benchmark, _r, runner = wire()
        benchmark.start(ADMIN, "prod-a", query_set="smoke", reason="first")
        with self.assertRaises(InvalidRequest):
            benchmark.start(ADMIN, "prod-a", query_set="smoke", reason="second")
        self.assertEqual(1, len(runner.started))

    def test_the_service_never_deactivates_a_backend(self):
        """⛔ CLAUDE.md rule 5. There is no path from here to set_active."""
        import inspect

        from tms.bench import service as module

        self.assertNotIn("set_active", inspect.getsource(module))

    # ── permissions and arguments ────────────────────────────────────

    def test_a_viewer_cannot_start_a_run(self):
        _c, _s, benchmark, _r, runner = wire(roles=("viewer",))
        with self.assertRaises(Forbidden):
            benchmark.start(VIEWER, "prod-a", query_set="smoke", reason="please")
        self.assertEqual([], runner.started)

    def test_an_undeclared_query_set_is_a_404(self):
        _c, _s, benchmark, _r, _runner = wire()
        with self.assertRaises(NotFound):
            benchmark.start(ADMIN, "prod-a", query_set="whatever", reason="please")

    def test_repetitions_outside_the_range_are_refused(self):
        _c, _s, benchmark, _r, _runner = wire()
        for value in (0, -1, 999, "many"):
            with self.assertRaises(InvalidRequest, msg=repr(value)):
                benchmark.start(ADMIN, "prod-a", query_set="smoke",
                                reason="please", repetitions=value)

    def test_only_runs_of_the_same_set_are_offered_for_comparison(self):
        _c, _s, benchmark, repository, _runner = wire()
        mine = repository.create(cluster="prod-a", query_set="smoke", actor="a",
                                 roles=["admin"], reason="r", repetitions=1,
                                 guard={"ok": True})
        repository.finish(mine["id"], "SUCCEEDED")
        other = repository.create(cluster="prod-a", query_set="other", actor="a",
                                  roles=["admin"], reason="r", repetitions=1,
                                  guard={"ok": True})
        repository.finish(other["id"], "SUCCEEDED")
        same = repository.create(cluster="prod-b", query_set="smoke", actor="a",
                                 roles=["admin"], reason="r", repetitions=1,
                                 guard={"ok": True})
        repository.finish(same["id"], "SUCCEEDED")

        offered = benchmark.comparable_runs(ADMIN, repository.get(mine["id"]))
        self.assertEqual([same["id"]], [r["id"] for r in offered])


@unittest.skipUnless(WEB_DEPS, "fastapi/httpx/jinja2/python-multipart not installed")
class BenchmarkScreenTest(unittest.IsolatedAsyncioTestCase):
    def build(self, **kwargs):
        config, service, benchmark, repository, runner = wire(**kwargs)
        self.repository = repository
        self.runner = runner
        return create_app(config=config, service=service, benchmark=benchmark)

    def client(self, app):
        return client_for(app)

    async def test_the_page_says_a_quiet_cluster_is_quiet(self):
        async with self.client(self.build()) as c:
            await sign_in(c)
            response = await c.get("/benchmark")
        self.assertEqual(200, response.status_code)
        self.assertIn("Quiet", response.text)
        self.assertIn('value="prod-a"', response.text)

    async def test_a_serving_cluster_is_selectable_and_says_so(self):
        """Labelled, not locked. Running against a live cluster is the point."""
        async with self.client(self.build(gateway_active=True)) as c:
            await sign_in(c)
            response = await c.get("/benchmark")
        self.assertEqual(200, response.status_code)
        self.assertIn("Serving traffic", response.text)
        self.assertNotIn("disabled>", response.text)
        # It still says what it saw, so the caveat is readable before running.
        self.assertIn("production queries are landing on it", response.text)

    async def test_every_cluster_is_offered_on_one_page(self):
        """One page, not one per cluster.

        The question that brings anyone here is "is A slower than B", and it
        used to be answered by typing two URLs and running the set twice.
        """
        async with self.client(self.build()) as c:
            await sign_in(c)
            response = await c.get("/benchmark")
        for cluster in ("prod-a", "prod-b"):
            self.assertIn('value="{}"'.format(cluster), response.text)

    async def test_the_old_per_cluster_address_still_leads_somewhere(self):
        async with self.client(self.build()) as c:
            await sign_in(c)
            response = await c.get("/clusters/prod-a/benchmark",
                                   follow_redirects=False)
        self.assertEqual(308, response.status_code)
        self.assertEqual("/benchmark", response.headers["location"])

    async def test_a_run_can_be_started_on_several_clusters_at_once(self):
        app = self.build()
        async with self.client(app) as c:
            await sign_in(c)
            response = await c.post("/benchmark", data={
                "clusters": ["prod-a", "prod-b"], "query_set": "smoke",
                "reason": "comparing the two", "repetitions": "1"})
        self.assertEqual(303, response.status_code)
        self.assertEqual({"prod-a", "prod-b"},
                         {r["cluster"] for r in self.repository.runs})

    async def test_a_serving_cluster_runs_alongside_a_quiet_one(self):
        """Both start; the difference is recorded, and the comparison warns."""
        app = self.build(gateway_active=False, refuse_cluster="prod-b")
        async with self.client(app) as c:
            await sign_in(c)
            response = await c.post("/benchmark", data={
                "clusters": ["prod-a", "prod-b"], "query_set": "smoke",
                "reason": "comparing the two", "repetitions": "1"})
        self.assertEqual(303, response.status_code)
        by_cluster = {r["cluster"]: r for r in self.repository.runs}
        self.assertEqual({"prod-a", "prod-b"}, set(by_cluster))
        self.assertTrue(by_cluster["prod-a"]["guard"]["ok"])
        self.assertFalse(by_cluster["prod-b"]["guard"]["ok"])

    async def test_a_run_on_a_serving_cluster_goes_through(self):
        app = self.build(gateway_active=True)
        async with self.client(app) as c:
            await sign_in(c)
            response = await c.post("/benchmark", data={
                "clusters": ["prod-a"], "query_set": "smoke",
                "reason": "the scheduled hourly probe", "repetitions": "1"})
        self.assertEqual(303, response.status_code)
        self.assertNotIn("error=", response.headers["location"])
        self.assertEqual(1, len(self.repository.runs))
        self.assertFalse(self.repository.runs[0]["guard"]["ok"])

    async def test_a_started_run_redirects_to_its_page(self):
        app = self.build()
        async with self.client(app) as c:
            await sign_in(c)
            response = await c.post("/benchmark", data={
                "clusters": ["prod-a"], "query_set": "smoke",
                "reason": "measuring", "repetitions": "2"})
            self.assertEqual(303, response.status_code)
            page = await c.get(response.headers["location"])
        self.assertEqual(200, page.status_code)
        self.assertIn("measuring", page.text)

    async def test_an_unguarded_run_says_so_on_its_own_page(self):
        app = self.build()
        run = self.repository.create(
            cluster="prod-a", query_set="smoke", actor="a", roles=["admin"],
            reason="taken during traffic", repetitions=1,
            guard={"ok": False, "advice": [{"code": "still_routed",
                                            "text": "This cluster was in rotation."}]})
        self.repository.finish(run["id"], "SUCCEEDED")
        async with self.client(app) as c:
            await sign_in(c)
            response = await c.get("/benchmarks/{}".format(run["id"]))
        self.assertIn("The cluster was serving traffic while this ran.",
                      response.text)
        self.assertIn("This cluster was in rotation.", response.text)

    async def test_a_comparison_renders_with_its_direction_in_words(self):
        app = self.build()
        ids = []
        for cluster, timing in (("prod-a", 100), ("prod-b", 250)):
            run = self.repository.create(
                cluster=cluster, query_set="smoke", actor="a", roles=["admin"],
                reason="r", repetitions=1, guard={"ok": True})
            self.repository.add_result(run["id"], {
                "query_name": "a", "iteration": 1, "state": "SUCCEEDED",
                "trino_query_id": "q", "elapsed_ms": timing,
                "trino_elapsed_ms": timing, "trino_cpu_ms": timing,
                "trino_queued_ms": 0, "trino_planning_ms": 0,
                "processed_rows": 1, "processed_bytes": 1,
                "peak_memory_bytes": 1, "error": None})
            self.repository.finish(run["id"], "SUCCEEDED")
            ids.append(run["id"])

        async with self.client(app) as c:
            await sign_in(c)
            response = await c.get("/benchmarks/{}?against={}".format(ids[1], ids[0]))
        self.assertEqual(200, response.status_code)
        # Colour alone would leave "is red good here" to the reader.
        self.assertIn("Slower", response.text)

    async def test_a_faster_query_shows_its_size_not_a_dash(self):
        """`duration` renders negatives as an em dash - right for an elapsed
        time, wrong for a difference. A 2% improvement read as "—"."""
        app = self.build()
        ids = []
        for cluster, timing in (("prod-a", 4200), ("prod-b", 4100)):
            run = self.repository.create(
                cluster=cluster, query_set="smoke", actor="a", roles=["admin"],
                reason="r", repetitions=1, guard={"ok": True})
            self.repository.add_result(run["id"], {
                "query_name": "a", "iteration": 1, "state": "SUCCEEDED",
                "trino_query_id": "q", "elapsed_ms": timing,
                "trino_elapsed_ms": timing, "trino_cpu_ms": timing,
                "trino_queued_ms": 0, "trino_planning_ms": 0,
                "processed_rows": 1, "processed_bytes": 1,
                "peak_memory_bytes": 1, "error": None})
            self.repository.finish(run["id"], "SUCCEEDED")
            ids.append(run["id"])

        async with self.client(app) as c:
            await sign_in(c)
            response = await c.get("/benchmarks/{}?against={}".format(ids[1], ids[0]))
        self.assertIn("-100ms", response.text)

    async def test_the_picker_polls_only_while_something_is_running(self):
        """⛔ The page itself must not reload on a timer.

        The form beside the picker holds a reason someone is halfway through
        typing. So one fieldset refreshes, and only while there is something
        to wait for — the polled copy carries the trigger, so when the last
        run finishes the swapped-in fieldset has none and polling stops.
        """
        app = self.build()
        async with self.client(app) as c:
            await sign_in(c)
            idle = await c.get("/benchmark/clusters")
            self.assertNotIn("hx-trigger", idle.text)

            self.repository.create(
                cluster="prod-a", query_set="smoke", actor="a", roles=["admin"],
                reason="r", repetitions=1, guard={"ok": True})
            busy = await c.get("/benchmark/clusters")
        self.assertIn('hx-trigger="every 5s"', busy.text)
        self.assertIn("A benchmark is already running here", busy.text)

    async def test_a_poll_keeps_the_clusters_already_ticked(self):
        """Without this the first poll silently un-chooses what was chosen."""
        import re

        app = self.build()
        async with self.client(app) as c:
            await sign_in(c)
            response = await c.get("/benchmark/clusters?clusters=prod-b")

        def ticked(name):
            # Matched over the whole <input>, not on the template's line
            # breaks - a reflow that changed nothing would fail that.
            pattern = r'<input[^>]*value="{}"[^>]*>'.format(name)
            found = re.search(pattern, response.text, re.S)
            self.assertIsNotNone(found, name)
            return "checked" in found.group(0)

        self.assertTrue(ticked("prod-b"))
        self.assertFalse(ticked("prod-a"))

    async def test_the_benchmark_page_still_has_no_page_level_refresh(self):
        async with self.client(self.build()) as c:
            await sign_in(c)
            response = await c.get("/benchmark")
        self.assertNotIn("data-refresh", response.text)

    async def test_an_unknown_run_is_a_404_page(self):
        async with self.client(self.build()) as c:
            await sign_in(c)
            response = await c.get("/benchmarks/999")
        self.assertEqual(404, response.status_code)

    async def test_aborting_asks_the_runner_to_stop(self):
        app = self.build()
        run = self.repository.create(
            cluster="prod-a", query_set="smoke", actor="a", roles=["admin"],
            reason="r", repetitions=1, guard={"ok": True})
        async with self.client(app) as c:
            await sign_in(c)
            response = await c.post("/benchmarks/{}/abort".format(run["id"]))
        self.assertEqual(303, response.status_code)
        self.assertEqual([run["id"]], self.runner.aborted)

    async def test_the_benchmark_pages_do_not_auto_refresh_the_start_form(self):
        """A timed reload would throw away the reason someone is typing."""
        async with self.client(self.build()) as c:
            await sign_in(c)
            response = await c.get("/benchmark")
        self.assertNotIn("data-refresh", response.text)


if __name__ == "__main__":
    unittest.main()
