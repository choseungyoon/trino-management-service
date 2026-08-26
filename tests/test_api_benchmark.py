"""The benchmark JSON API.

The service's rules have their own tests. What is checked here is that they
actually reach an HTTP caller - a service that refuses and a route that
returns 200 anyway would pass every test in test_benchmark_sets.py.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)

try:
    import httpx  # noqa: F401
    from fastapi import FastAPI  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

from tms.api.main import create_app  # noqa: E402
from tms.bench.queryset import build_query_sets  # noqa: E402
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

from console import build_service, client_for, sign_in  # noqa: E402

SETS = {"smoke": {"title": "Smoke", "queries": [
    {"name": "a", "sql": "SELECT 1"},
    {"name": "b", "sql": "SELECT 2"},
]}}


class Gateway:
    """Both backends deactivated, so the guard reports a quiet cluster."""

    def __init__(self, clusters):
        self.clusters = clusters

    def list_backends(self, active_only=False):
        return [{"name": "trino-{}-1".format(c), "active": False}
                for c in self.clusters]


class RecordingRunner:
    def __init__(self):
        self.started, self.aborted = [], []

    def start(self, run, query_set, repetitions):
        self.started.append((run["id"], query_set.key, repetitions))

    def abort(self, run_id):
        self.aborted.append(run_id)


@unittest.skipUnless(WEB_DEPS, "fastapi/httpx not installed")
class BenchmarkApiTest(unittest.IsolatedAsyncioTestCase):
    def build(self, roles=("admin",)):
        config, service, _trino = build_service(roles=roles,
                                                clusters=("prod-a", "prod-b"))
        now = utcnow()
        service.repository.save(Snapshot(GATEWAY_SCOPE, KIND_GATEWAY, now, payload={
            "backends": [{"name": "trino-{}-1".format(c), "cluster": c,
                          "active": False} for c in config.cluster_names]}))
        for cluster in config.cluster_names:
            service.repository.save(Snapshot(cluster, KIND_QUERIES, now, payload={
                "summary": {"running": 0, "queued": 0, "total": 0}, "queries": []}))
        self.repository = InMemoryBenchmarkRepository()
        self.runner = RecordingRunner()
        self.service = service
        benchmark = BenchmarkService(
            config=config, snapshots=service.repository,
            audit_guard=service.audit, repository=self.repository,
            runner=self.runner,
            query_sets=InMemoryQuerySetRepository(build_query_sets(SETS)),
            gateway_client=Gateway(config.cluster_names))
        return create_app(config=config, service=service, benchmark=benchmark)

    async def signed_in(self, app=None):
        client = client_for(app or self.build())
        await client.__aenter__()
        await sign_in(client)
        self.addAsyncCleanup(client.__aexit__, None, None, None)
        return client

    # ── reading ──────────────────────────────────────────────────────

    async def test_the_overview_lists_every_cluster_and_set(self):
        c = await self.signed_in()
        body = (await c.get("/api/v1/benchmark")).json()
        self.assertEqual({"prod-a", "prod-b"},
                         {x["name"] for x in body["clusters"]})
        self.assertEqual(["smoke"], [s["key"] for s in body["query_sets"]])

    async def test_a_set_comes_back_with_its_queries(self):
        c = await self.signed_in()
        body = (await c.get("/api/v1/benchmark/sets/smoke")).json()
        self.assertEqual(["a", "b"],
                         sorted(q["name"] for q in body["set"]["queries"]))

    async def test_an_unknown_set_is_404_not_500(self):
        c = await self.signed_in()
        self.assertEqual(404, (await c.get("/api/v1/benchmark/sets/nope")).status_code)

    # ── writing ──────────────────────────────────────────────────────

    async def test_a_run_starts_and_reports_what_started(self):
        c = await self.signed_in()
        response = await c.post("/api/v1/benchmark", json={
            "clusters": ["prod-a", "prod-b"], "query_set": "smoke",
            "reason": "comparing the two", "repetitions": 2})
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual({"prod-a", "prod-b"},
                         {r["cluster"] for r in body["started"]})
        self.assertEqual([], body["refused"])
        self.assertEqual(2, len(self.runner.started))

    async def test_a_write_statement_is_refused_over_http_too(self):
        """⛔ The allowlist has to survive the trip, not just exist."""
        c = await self.signed_in()
        response = await c.put("/api/v1/benchmark/sets/smoke/queries/bad", json={
            "statement": "DELETE FROM t", "reason": "trying it"})
        self.assertEqual(400, response.status_code)
        self.assertIn("read-only", response.text)

    async def test_a_missing_reason_is_400(self):
        c = await self.signed_in()
        response = await c.put("/api/v1/benchmark/sets/smoke/queries/c", json={
            "statement": "SELECT 3", "reason": "   "})
        self.assertEqual(400, response.status_code)

    async def test_a_viewer_cannot_start_a_run(self):
        c = await self.signed_in(self.build(roles=("viewer",)))
        response = await c.post("/api/v1/benchmark", json={
            "clusters": ["prod-a"], "query_set": "smoke", "reason": "please"})
        self.assertEqual(403, response.status_code)
        self.assertEqual([], self.repository.runs)

    async def test_a_set_is_created_with_its_first_query(self):
        c = await self.signed_in()
        response = await c.post("/api/v1/benchmark/sets", json={
            "key": "nightly", "title": "Nightly", "name": "scan",
            "statement": "SELECT count(*) FROM t", "reason": "tracking the load"})
        self.assertEqual(201, response.status_code)
        self.assertEqual(["scan"], [q["name"] for q in response.json()["queries"]])

    async def test_deleting_a_query_returns_no_content(self):
        c = await self.signed_in()
        response = await c.delete(
            "/api/v1/benchmark/sets/smoke/queries/b?reason=no+longer+useful")
        self.assertEqual(204, response.status_code)
        body = (await c.get("/api/v1/benchmark/sets/smoke")).json()
        self.assertEqual(["a"], [q["name"] for q in body["set"]["queries"]])

    # ── the properties that must not drift ───────────────────────────

    async def test_a_run_returns_timings_and_never_result_rows(self):
        """⛔ The line between this and a SQL editor is the output, not the
        input. A run gives back timings; the rows Trino produced are counted
        and discarded, and no endpoint can hand them back."""
        c = await self.signed_in()
        run = self.repository.create(
            cluster="prod-a", query_set="smoke", actor="a", roles=["admin"],
            reason="r", repetitions=1, guard={"ok": True}, queries=[])
        self.repository.add_result(run["id"], {
            "query_name": "a", "iteration": 1, "state": "SUCCEEDED",
            "trino_query_id": "q", "elapsed_ms": 100, "trino_elapsed_ms": 90,
            "trino_cpu_ms": 70, "trino_queued_ms": 1, "trino_planning_ms": 2,
            "processed_rows": 15000, "processed_bytes": 4096,
            "peak_memory_bytes": 8192, "error": None})
        self.repository.finish(run["id"], "SUCCEEDED")

        body = (await c.get("/api/v1/benchmarks/{}".format(run["id"]))).json()
        for result in body["results"]:
            # processed_rows is a count of rows read, not the rows themselves.
            self.assertIsInstance(result["processed_rows"], int)
            for forbidden in ("rows", "data", "values", "columns", "result"):
                self.assertNotIn(forbidden, result, forbidden)

    async def test_everything_needs_a_session_and_says_401_not_500(self):
        """⛔ This caught a real one.

        The Unauthenticated handler re-raised for /api/ paths, expecting the
        ApiError handler to pick it up. Starlette does not re-run its handler
        lookup on an exception raised inside a handler, so every
        unauthenticated API request was a 500 - which a client cannot tell
        from a broken server, and cannot recover from by signing in.
        """
        client = client_for(self.build())
        async with client:
            for path in ("/api/v1/benchmark", "/api/v1/benchmark/sets",
                         "/api/v1/benchmarks/1", "/api/v1/me"):
                response = await client.get(path)
                self.assertEqual(401, response.status_code, path)
                self.assertEqual("UNAUTHENTICATED",
                                 response.json()["error"]["code"], path)

    async def test_the_api_is_absent_when_the_feature_is_off(self):
        """⛔ 503 with a name, not 404. A disabled feature and a missing one
        look identical otherwise, and that sends whoever is debugging to the
        wrong place."""
        config, service, _trino = build_service()
        client = client_for(create_app(config=config, service=service))
        async with client:
            await sign_in(client)
            response = await client.get("/api/v1/benchmark")
        self.assertEqual(503, response.status_code)
        self.assertIn("benchmark", response.text)


if __name__ == "__main__":
    unittest.main()
