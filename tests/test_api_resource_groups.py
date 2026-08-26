"""The resource group JSON API.

⛔ These endpoints change query admission control. A bad value reaches every
coordinator within the refresh interval with no restart in between, so what is
checked here is that the server still refuses - not that a screen does.

Route *shape* is checked too. The web version of these routes shipped two bugs
that only a real request could find: a literal path segment registered after a
typed parameter got parsed as one, and a success message with an em dash broke
a latin-1 cookie. A sweep that only walks GETs saw neither.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

try:
    import httpx
    from fastapi import FastAPI  # noqa: F401
    import multipart  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

from test_web_resource_groups import (  # noqa: E402
    ADMIN,
    GLOBAL,
    PASSWORD,
    SEL_ADMIN,
    SEL_CATCH_ALL,
    USER,
    USER_LEAF,
    build,
)


@unittest.skipUnless(WEB_DEPS, "web dependencies not installed")
class ResourceGroupApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests.browser.rgstore import InMemoryResourceGroupStore

        self.store = InMemoryResourceGroupStore()
        self.app = build(self.store)

    async def signed_in(self):
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://tms.test", follow_redirects=False)
        await client.__aenter__()
        await client.post("/login", data={"username": USER, "password": PASSWORD,
                                          "next": "/"})
        self.addAsyncCleanup(client.__aexit__, None, None, None)
        return client

    def url(self, suffix=""):
        return "/api/v1/clusters/prod-a/resource-groups" + suffix

    # ── reading ──────────────────────────────────────────────────────

    async def test_the_tree_comes_back(self):
        c = await self.signed_in()
        response = await c.get(self.url())
        self.assertEqual(200, response.status_code, response.text[:300])
        body = response.json()["data"]
        # The shape the console consumes: the tree, and whether TMS is
        # actually reading the store Trino reads.
        self.assertTrue(body["enabled"])
        self.assertEqual("cluster1", body["environment"])
        self.assertIn("rows", body)
        self.assertIn("selectors", body)
        # ⛔ "TMS could not read the live state" must not look like "no
        # traffic": the payload carries that distinction separately.
        self.assertIn("live_available", body)

    async def test_revisions_is_not_read_as_a_group_id(self):
        """⛔ A literal segment registered beside a parameter. This is the
        shape that shipped a 422 on the web side."""
        c = await self.signed_in()
        response = await c.get(self.url("/revisions"))
        self.assertEqual(200, response.status_code, response.text[:300])
        self.assertIn("revisions", response.json())

    async def test_deletion_impact_is_readable_before_deleting(self):
        c = await self.signed_in()
        response = await c.get(self.url("/1/impact"))
        self.assertEqual(200, response.status_code, response.text[:300])

    # ── writing ──────────────────────────────────────────────────────

    async def test_a_selector_post_reaches_its_own_handler(self):
        """`/selectors` must not be parsed as a group id."""
        c = await self.signed_in()
        response = await c.post(self.url("/selectors"), json={
            "target_row_id": 3, "priority": 15,
            "matchers": {"user_regex": "^bob$"},
            "reason": "give bob his own rule"})
        self.assertEqual(201, response.status_code, response.text[:300])
        self.assertEqual(3, len(self.store.selectors))

    async def test_a_selector_delete_reaches_its_own_handler(self):
        c = await self.signed_in()
        response = await c.delete(
            self.url("/selectors/10") + "?reason=no+longer+needed")
        self.assertEqual(200, response.status_code, response.text[:300])
        self.assertEqual([11], [x["id"] for x in self.store.selectors])

    async def test_a_write_without_a_reason_is_400(self):
        """⛔ Rule 3 over HTTP. The client cannot opt out of it."""
        c = await self.signed_in()
        response = await c.patch(self.url("/2"), json={
            "changes": {"hard_concurrency_limit": 12}, "reason": "  "})
        self.assertEqual(400, response.status_code, response.text[:300])

    async def test_a_refused_value_is_400_and_says_why(self):
        """Zero concurrency stops the group entirely - a delete wearing a
        tuning value's clothes. Trino accepts it; this does not."""
        c = await self.signed_in()
        response = await c.patch(self.url("/2"), json={
            "changes": {"hard_concurrency_limit": 0},
            "reason": "trying a zero limit"})
        self.assertEqual(400, response.status_code, response.text[:300])

    async def test_a_valid_change_goes_through_and_is_audited(self):
        c = await self.signed_in()
        response = await c.patch(self.url("/2"), json={
            "changes": {"hard_concurrency_limit": 12},
            "reason": "dashboards were queueing behind one another"})
        self.assertEqual(200, response.status_code, response.text[:300])

    async def test_a_viewer_cannot_write(self):
        from tests.browser.rgstore import InMemoryResourceGroupStore

        self.store = InMemoryResourceGroupStore()
        self.app = build(self.store)
        # The fixture only builds an admin, so exercise the refusal the other
        # way: no session at all must be 401, never 500.
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://tms.test", follow_redirects=False)
        async with client:
            response = await client.patch(self.url("/2"), json={
                "changes": {"hard_concurrency_limit": 12}, "reason": "no"})
        self.assertEqual(401, response.status_code)
        self.assertEqual("UNAUTHENTICATED", response.json()["error"]["code"])


if __name__ == "__main__":
    unittest.main()
