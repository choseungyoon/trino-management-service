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


if __name__ == "__main__":
    unittest.main()
