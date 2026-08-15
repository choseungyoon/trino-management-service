"""The Resource Groups screen renders (FR-WL-07).

The service-layer tests cover what the payload says. These cover the part unit
tests cannot: that the template actually renders it, and that the two states
worth interrupting an operator over - a missing catch-all selector, and a group
running with no configuration behind it - reach the page.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

try:
    import httpx  # noqa: F401
    import jinja2  # noqa: F401
    import multipart  # noqa: F401
    from fastapi import FastAPI  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

from tms.api.main import create_app  # noqa: E402
from tms.api.services import TmsService  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    KIND_RESOURCE_GROUPS,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.core.audit import AuditGuard, InMemoryAuditRepository  # noqa: E402
from tms.core.config import build_config  # noqa: E402
from tms.core.passwords import hash_password  # noqa: E402

# The row fixtures and the fake store live with the service-layer tests; there
# is one definition of what a resource_groups row looks like, not two.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_resource_group_config import (  # noqa: E402
    ADMIN,
    GLOBAL,
    SEL_ADMIN,
    SEL_CATCH_ALL,
    USER_LEAF,
    FakeStore,
)

USER = "op"
PASSWORD = "correct horse battery staple"


def build(store, workload_enabled=True, live=None):
    repository = InMemorySnapshotRepository()
    if live is not None:
        repository.save(Snapshot("prod-a", KIND_RESOURCE_GROUPS, utcnow(),
                                 payload={"groups": live}))
    config = build_config({
        "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid:8443",
                      "expected_workers": 11, "node_environment": "cluster1"}],
        "trino": {"user": "tms-svc", "password": "pw"},
        "database": {"url": "postgresql://u:p@h:5432/d"},
        "collector": {"stale_threshold_seconds": 600},
        "workload": {"enabled": workload_enabled},
        "resource_groups": {"enabled": True},
        "portal": {
            "session_secret": "s" * 48,
            "local_users": {USER: {"password_hash": hash_password(PASSWORD, iterations=1000),
                                   "roles": ["admin"]}},
        },
    })
    audit = InMemoryAuditRepository()
    service = TmsService(
        config=config, repository=repository, audit_guard=AuditGuard(audit),
        audit_repository=audit, trino_clients={}, config_store=store)
    return create_app(config=config, service=service)


@unittest.skipUnless(WEB_DEPS, "web dependencies not installed")
class ResourceGroupPageTest(unittest.IsolatedAsyncioTestCase):
    def client(self, app):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://tms.test", follow_redirects=False)

    async def get(self, app):
        async with self.client(app) as c:
            await c.post("/login", data={"username": USER, "password": PASSWORD, "next": "/"})
            return await c.get("/clusters/prod-a/resource-groups")

    async def test_the_configured_tree_renders(self):
        store = FakeStore(groups=[GLOBAL, USER_LEAF, ADMIN],
                          selectors=[SEL_ADMIN, SEL_CATCH_ALL])
        response = await self.get(build(store, live=[{"id": "global", "running": 2}]))
        self.assertEqual(200, response.status_code)
        body = response.text
        self.assertIn("global", body)
        self.assertIn("cluster1", body)
        self.assertIn("everything else", body, "the catch-all is named, not left blank")

    async def test_a_group_that_never_exports_is_not_reported_as_idle(self):
        """"not exported" and "no traffic yet" are different facts."""
        store = FakeStore(groups=[GLOBAL, USER_LEAF], selectors=[SEL_CATCH_ALL])
        body = (await self.get(build(store, live=[{"id": "global", "running": 1}]))).text
        self.assertIn("not exported", body)

    async def test_a_missing_catch_all_is_raised_to_the_top(self):
        """V10 - Trino 477 leaves unmatched queries undocumented."""
        store = FakeStore(groups=[GLOBAL, ADMIN], selectors=[SEL_ADMIN])
        body = (await self.get(build(store, live=[]))).text
        self.assertIn("No catch-all selector", body)

    async def test_a_group_with_no_configuration_behind_it_is_flagged(self):
        store = FakeStore(groups=[GLOBAL], selectors=[SEL_CATCH_ALL])
        body = (await self.get(build(store, live=[{"id": "legacy.batch", "running": 4}]))).text
        self.assertIn("legacy.batch", body)
        self.assertIn("no configuration behind them", body)

    async def test_with_workload_off_the_blank_column_is_explained(self):
        store = FakeStore(groups=[GLOBAL], selectors=[SEL_CATCH_ALL])
        body = (await self.get(build(store, workload_enabled=False))).text
        self.assertIn("workload.enabled", body)
        self.assertIn("not because the groups are idle", body)

    async def test_the_nav_link_is_hidden_when_the_store_is_not_configured(self):
        """A link to a page that can only say "off" is noise."""
        config = build_config({
            "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid:8443",
                          "expected_workers": 11}],
            "trino": {"user": "tms-svc", "password": "pw"},
            "database": {"url": "postgresql://u:p@h:5432/d"},
            "portal": {
                "session_secret": "s" * 48,
                "local_users": {USER: {"password_hash": hash_password(PASSWORD, iterations=1000),
                                       "roles": ["admin"]}},
            },
        })
        audit = InMemoryAuditRepository()
        service = TmsService(
            config=config, repository=InMemorySnapshotRepository(),
            audit_guard=AuditGuard(audit), audit_repository=audit, trino_clients={})
        app = create_app(config=config, service=service)
        async with self.client(app) as c:
            await c.post("/login", data={"username": USER, "password": PASSWORD, "next": "/"})
            body = (await c.get("/")).text
        self.assertNotIn("/resource-groups", body)


@unittest.skipUnless(WEB_DEPS, "web dependencies not installed")
class WritePathTest(unittest.IsolatedAsyncioTestCase):
    """The POST routes.

    Nothing covered these until two of them failed in a browser: the selector
    routes answered 422 because a literal path segment was registered after an
    int-typed `{row_id}` and got parsed as one, and revert answered 500 because
    its success message contained an em dash and cookies are latin-1. The screen
    sweep in test_web_restart only walks GET routes, so both were invisible.
    """

    def setUp(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from tests.browser.rgstore import InMemoryResourceGroupStore

        self.store = InMemoryResourceGroupStore()
        self.app = build(self.store)

    def client(self):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://tms.test", follow_redirects=False)

    async def post(self, client, path, data):
        await client.post("/login", data={"username": USER, "password": PASSWORD,
                                          "next": "/"})
        return await client.post("/clusters/prod-a" + path, data=data)

    async def test_adding_a_selector_reaches_its_own_handler(self):
        """`/resource-groups/selectors` must not be read as a row id."""
        async with self.client() as c:
            response = await self.post(c, "/resource-groups/selectors", {
                "priority": "15", "matcher": "user_regex", "pattern": "^bob$",
                "target_row_id": "3", "reason": "give bob his own rule"})
        self.assertEqual(200, response.status_code, response.text[:400])
        self.assertEqual(3, len(self.store.selectors))

    async def test_deleting_a_selector_reaches_its_own_handler(self):
        async with self.client() as c:
            response = await self.post(
                c, "/resource-groups/selectors/10/delete", {"reason": "no longer used"})
        self.assertEqual(200, response.status_code)
        self.assertEqual([11], [s["id"] for s in self.store.selectors])

    async def test_saving_a_group_still_matches_the_row_id_route(self):
        """Putting the literal routes first must not shadow this one."""
        async with self.client() as c:
            response = await self.post(c, "/resource-groups/2", {
                "name": "${USER}", "hard_concurrency_limit": "12",
                "max_queued": "100", "soft_memory_limit": "30%",
                "scheduling_policy": "fair", "reason": "dashboards were queueing"})
        self.assertEqual(200, response.status_code)
        leaf = next(g for g in self.store.groups if g["row_id"] == 2)
        self.assertEqual(12, leaf["hard_concurrency_limit"])

    async def test_adding_a_group_reaches_the_collection_route(self):
        async with self.client() as c:
            response = await self.post(c, "/resource-groups", {
                "name": "reporting", "parent_row_id": "", "jmx_export": "1",
                "hard_concurrency_limit": "10", "max_queued": "100",
                "reason": "new team"})
        self.assertEqual(200, response.status_code)
        self.assertIn("reporting", [g["name"] for g in self.store.groups])

    async def test_reverting_redirects_instead_of_failing_on_its_own_message(self):
        """The success message carries an em dash; a cookie holds latin-1."""
        async with self.client() as c:
            response = await self.post(
                c, "/resource-groups/history/1/revert", {"reason": "made it worse"})
        self.assertEqual(303, response.status_code, response.text[:400])
        self.assertIn("/resource-groups/history", response.headers["location"])

    async def test_the_flash_message_survives_the_round_trip_intact(self):
        """Percent-encoding must not leak into what the operator reads."""
        async with self.client() as c:
            await self.post(c, "/resource-groups/history/1/revert",
                            {"reason": "made it worse"})
            page = await c.get("/clusters/prod-a/resource-groups/history")
        self.assertIn("Reverted.", page.text)
        self.assertNotIn("%20", page.text.split("Reverted.")[1][:200])


if __name__ == "__main__":
    unittest.main()
