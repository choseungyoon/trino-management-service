"""Drive the operator console through ASGI, with no infrastructure.

routes.py was at 0% coverage while the UI ran in production. The only thing
exercising it was an ad-hoc smoke script living in a scratch directory outside
the repository - so the day that directory was cleaned, the sole verification
of every screen would have vanished with it.

Nothing here needs PostgreSQL or Trino: in-memory repositories and a stub
client stand in, which is enough to exercise routing, session handling,
permissions, the write ceremonies and the degraded paths. What it deliberately
does not cover is real SQL and real Trino behaviour - that stays in
tests/integration/.

Skipped when fastapi/httpx/jinja2 are absent, so the suite still runs on a bare
interpreter.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

try:
    import httpx
    from fastapi import FastAPI  # noqa: F401
    from jinja2 import Environment  # noqa: F401
    import multipart  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

from tms.api.main import create_app  # noqa: E402
from tms.api.services import TmsService  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    KIND_HEALTH,
    KIND_QUERIES,
    KIND_RESOURCE_GROUPS,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.core.audit import AuditGuard, InMemoryAuditRepository  # noqa: E402
from tms.core.config import build_config  # noqa: E402
from tms.core.passwords import hash_password  # noqa: E402

USER = "operator1"
PASSWORD = "Console-Test-9!"


class StubTrino:
    """Stands in for a coordinator. Records kills so the ceremony is testable."""

    def __init__(self):
        self.killed = []

    def kill_query(self, query_id, message):
        self.killed.append((query_id, message))

    def get_query(self, query_id):
        return {"queryId": query_id, "query": "SELECT 1", "state": "RUNNING"}


def build_service(roles=("admin",), with_data=True, workload=None,
                  clusters=("prod-a",)):
    repository = InMemorySnapshotRepository()
    now = utcnow()
    if with_data:
        repository.save(Snapshot("prod-a", KIND_QUERIES, now, payload={
            "summary": {"running": 1, "queued": 0, "long_running": 1, "total": 1},
            "queries": [{
                "query_id": "20260808_000000_00001_abcde", "state": "RUNNING",
                "user": "analyst", "source": "superset",
                "resource_group_id": ["global", "adhoc"],
                "elapsed_ms": 425000.0, "queued_ms": 12.0, "total_cpu_ms": 900.0,
                "peak_user_memory_bytes": 1048576, "physical_input_bytes": 2048,
                "progress_percentage": 42.0, "running_drivers": 3,
                "queued_drivers": 0, "fully_blocked": False,
                "query_preview": "SELECT count(*) FROM t", "query_truncated": False,
                "long_running": True,
            }],
        }))
        repository.save(Snapshot("prod-a", KIND_HEALTH, now, payload={
            "rollup_state": "CONCERNING", "rollup_enabled": True, "stale": False,
            "tests": [
                {"id": "H-01", "name": "Coordinator responsiveness", "state": "GOOD",
                 "observed_value": "responsive", "advice": ""},
                {"id": "H-05", "name": "Query failure rate (5m)", "state": "CONCERNING",
                 "observed_value": 7.5, "threshold": 20,
                 "advice": "7.5% of queries failed in the last 5 minutes."},
            ],
        }))

    config = build_config({
        # One cluster by default: most screens are per-cluster and a second one
        # would double every count the other tests assert on. The benchmark
        # screen is the exception - it is about comparing two - so it asks.
        "clusters": [{"name": name,
                      "coordinator_url": "https://{}.invalid:8443".format(name),
                      "expected_workers": 12,
                      "trino_ui_url": "https://{}.invalid:8443/ui/".format(name)}
                     for name in clusters],
        "trino": {"user": "tms-svc", "password": "pw"},
        "database": {"url": "postgresql://u:p@h:5432/d"},
        "collector": {"stale_threshold_seconds": 600},
        "deeplinks": {"superset_url": "https://superset.invalid/"},
        "workload": workload or {},
        "portal": {
            "session_secret": "s" * 48,
            "local_users": {USER: {"password_hash": hash_password(PASSWORD, iterations=1000),
                                   "roles": list(roles)}},
        },
    })
    trino = StubTrino()
    # One repository, not two - the guard writes and the service reads through
    # the same object, exactly as in production.
    audit = InMemoryAuditRepository()
    service = TmsService(
        config=config, repository=repository, audit_guard=AuditGuard(audit),
        audit_repository=audit, trino_clients={"prod-a": trino},
    )
    return config, service, trino


def client_for(app):
    """ASGITransport is async-only in httpx 0.28, so every request is awaited."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://tms.test", follow_redirects=False,
    )


async def sign_in(client, password=PASSWORD):
    return await client.post(
        "/login", data={"username": USER, "password": password, "next": "/"})


@unittest.skipUnless(WEB_DEPS, "fastapi/httpx/jinja2/python-multipart not installed")
class WebRouteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config, self.service, self.trino = build_service()
        self.app = create_app(config=self.config, service=self.service)

    def client(self):
        return client_for(self.app)

    def login(self, client, password=PASSWORD):
        return sign_in(client, password)

    # ------------------------------------------------------------- auth gate

    async def test_anonymous_is_redirected_to_login(self):
        async with self.client() as c:
            response = await c.get("/")
        self.assertEqual(303, response.status_code)
        self.assertIn("/login", response.headers["location"])

    async def test_bad_password_is_rejected_without_a_session(self):
        async with self.client() as c:
            response = await self.login(c, password="wrong")
            self.assertEqual(401, response.status_code)
            self.assertNotIn("tms_session", c.cookies)

    async def test_login_issues_a_secure_cookie(self):
        """Secure matters: the whole HTTPS deployment requirement rests on it."""
        async with self.client() as c:
            response = await self.login(c)
            self.assertEqual(303, response.status_code)
            cookie = response.headers.get("set-cookie", "")
        self.assertIn("tms_session", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)

    # ---------------------------------------------------------------- screens

    async def test_every_screen_renders_for_a_signed_in_operator(self):
        pages = ["/", "/queries", "/clusters/prod-a/queries",
                 "/clusters/prod-a/health", "/audit", "/audit/export", "/account"]
        async with self.client() as c:
            await self.login(c)
            for path in pages:
                response = await c.get(path)
                self.assertEqual(200, response.status_code, path)
                self.assertIn("<html", response.text.lower(), path)

    async def test_live_query_data_reaches_the_page(self):
        async with self.client() as c:
            await self.login(c)
            body = (await c.get("/clusters/prod-a/queries")).text
        self.assertIn("analyst", body)
        self.assertIn("superset", body)

    async def test_health_advice_is_shown_when_not_good(self):
        """A CONCERNING test without its advice on screen is useless."""
        async with self.client() as c:
            await self.login(c)
            body = (await c.get("/clusters/prod-a/health")).text
        self.assertIn("7.5% of queries failed", body)

    async def test_unknown_cluster_renders_an_error_page_not_a_traceback(self):
        async with self.client() as c:
            await self.login(c)
            response = await c.get("/clusters/nope/health")
        self.assertEqual(404, response.status_code)
        self.assertIn("<html", response.text.lower())

    # -------------------------------------------------------- write ceremony

    async def test_kill_requires_a_reason(self):
        async with self.client() as c:
            await self.login(c)
            response = await c.post("/clusters/prod-a/queries/q1/kill", data={"reason": "   "})
        self.assertEqual(400, response.status_code)
        self.assertEqual([], self.trino.killed, "a blank reason must not reach Trino")

    async def test_kill_with_a_reason_is_delivered_and_audited(self):
        async with self.client() as c:
            await self.login(c)
            response = await c.post("/clusters/prod-a/queries/q1/kill",
                              data={"reason": "runaway query, paging the owner"})
        self.assertIn(response.status_code, (200, 303))
        self.assertEqual(1, len(self.trino.killed))
        records = self.service.audit_repository.records
        self.assertTrue(any("runaway query" in r.reason for r in records))

    async def test_export_requires_a_reason(self):
        async with self.client() as c:
            await self.login(c)
            response = await c.post("/audit/export", data={"reason": ""})
            self.assertEqual(400, response.status_code)

    async def test_export_returns_csv(self):
        async with self.client() as c:
            await self.login(c)
            await c.post("/clusters/prod-a/queries/q1/kill", data={"reason": "for the export"})
            response = await c.post("/audit/export", data={"reason": "quarterly audit"})
        self.assertEqual(200, response.status_code)
        self.assertIn("occurred_at", response.text)
        self.assertIn("for the export", response.text)

    async def test_export_with_no_matching_rows_still_has_a_header(self):
        """A 0-byte file cannot be told apart from a broken export. The person
        who asked for this is holding it as evidence."""
        async with self.client() as c:
            await self.login(c)
            response = await c.post("/audit/export", data={"reason": "nothing to find"})
        self.assertEqual(200, response.status_code)
        self.assertIn("occurred_at", response.text)
        self.assertEqual(1, len(response.text.strip().splitlines()),
                         "header only, no data rows")

    # ------------------------------------------------------------ degradation

    async def test_screens_survive_with_no_collected_data(self):
        """Before the collector's first tick every screen must still render."""
        config, service, _ = build_service(with_data=False)
        app = create_app(config=config, service=service)
        async with client_for(app) as c:
            await c.post("/login", data={"username": USER, "password": PASSWORD, "next": "/"})
            for path in ("/", "/queries", "/clusters/prod-a/health"):
                self.assertEqual(200, (await c.get(path)).status_code, path)

    # ------------------------------------------------------------ permissions

    async def test_viewer_cannot_reach_the_kill_form(self):
        config, service, trino = build_service(roles=("viewer",))
        app = create_app(config=config, service=service)
        async with client_for(app) as c:
            await c.post("/login", data={"username": USER, "password": PASSWORD, "next": "/"})
            response = await c.post("/clusters/prod-a/queries/q1/kill", data={"reason": "nope"})
        self.assertEqual(403, response.status_code)
        self.assertEqual([], trino.killed)

    # -------------------------------------------------------------- workload

    async def test_workload_says_when_collection_is_off(self):
        """Off is a configuration choice; empty is a possible misconfiguration.
        They render identically in the data, so the page must distinguish them
        or people go hunting through resource-groups.json for nothing."""
        async with self.client() as c:
            await self.login(c)
            body = (await c.get("/clusters/prod-a/workload")).text
        self.assertIn("collection is off", body)
        self.assertNotIn("jmxExport", body)

    async def test_workload_renders_the_group_tree(self):
        config, service, _ = build_service(workload={"enabled": True})
        service.repository.save(Snapshot(
            "prod-a", KIND_RESOURCE_GROUPS, utcnow(),
            payload={
                "complete": False,
                "summary": {"groups": 2, "running": 3, "queued": 4,
                            "blocked_groups": 1,
                            "blocked": [{"id": "global.adhoc",
                                         "reason": "concurrency_limit", "queued": 4}]},
                "groups": [], "tree": [{
                    "id": "global", "name": "global", "depth": 0, "path": ["global"],
                    "running": 3, "queued": 4, "hard_concurrency_limit": None,
                    "max_queued": None, "cpu_ms": 1000, "memory_bytes": 1024,
                    "bottleneck": None,
                    "children": [{
                        "id": "global.adhoc", "name": "adhoc", "depth": 1,
                        "path": ["global", "adhoc"], "running": 3, "queued": 4,
                        "hard_concurrency_limit": 3, "max_queued": 100,
                        "cpu_ms": 900, "memory_bytes": 512,
                        "bottleneck": "concurrency_limit", "children": []}],
                }],
            }))
        app = create_app(config=config, service=service)
        async with client_for(app) as c:
            await sign_in(c)
            body = (await c.get("/clusters/prod-a/workload")).text
        self.assertIn("adhoc", body)
        self.assertIn("At concurrency limit", body, "the diagnosis must be in words")
        self.assertIn("lazily", body, "the incompleteness caveat must be on screen")

    # ----------------------------------------------------------------- theme

    async def test_theme_toggle_sets_a_cookie_and_returns(self):
        async with self.client() as c:
            await self.login(c)
            response = await c.post("/ui/theme", data={"next": "/"})
        self.assertEqual(303, response.status_code)
        self.assertIn("tms_theme", response.headers.get("set-cookie", ""))

    async def test_logout_clears_the_session(self):
        async with self.client() as c:
            await self.login(c)
            await c.post("/logout")
            self.assertEqual(303, (await c.get("/")).status_code)


if __name__ == "__main__":
    unittest.main()
