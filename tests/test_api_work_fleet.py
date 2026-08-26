"""The work board and fleet JSON APIs.

Both carry rules that only mean anything if they survive the trip: the board
may only mint requests, and a node shutdown needs a reason.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

try:
    import httpx  # noqa: F401
    from fastapi import FastAPI  # noqa: F401
    import multipart  # noqa: F401

    WEB_DEPS = True
except ImportError:  # pragma: no cover - environment dependent
    WEB_DEPS = False

from tms.api.main import create_app  # noqa: E402
from tms.work.seed import seed as seed_board  # noqa: E402
from tms.work.service import BoardService  # noqa: E402
from tms.work.store import InMemoryBoardRepository  # noqa: E402

from test_web_routes import build_service, client_for, sign_in  # noqa: E402


@unittest.skipUnless(WEB_DEPS, "web dependencies are not installed")
class WorkApiTest(unittest.IsolatedAsyncioTestCase):
    def build(self, roles=("admin",)):
        config, service, _trino = build_service(roles=roles)
        self.repository = InMemoryBoardRepository()
        seed_board(self.repository)
        return create_app(config=config, service=service,
                          board=BoardService(self.repository))

    async def signed_in(self, app=None):
        client = client_for(app or self.build())
        await client.__aenter__()
        await sign_in(client)
        self.addAsyncCleanup(client.__aexit__, None, None, None)
        return client

    async def test_the_board_comes_back_in_columns(self):
        c = await self.signed_in()
        body = (await c.get("/api/v1/work")).json()
        self.assertTrue(body["available"])
        self.assertTrue(body["columns"])

    async def test_one_item_carries_its_timeline(self):
        c = await self.signed_in()
        body = (await c.get("/api/v1/work/W-1")).json()
        self.assertEqual("W-1", body["key"])
        # ⛔ Interleaved by the server. Two clients doing it themselves would
        # be two copies of a rule nobody wrote down.
        self.assertIn("timeline", body)

    async def test_a_raised_item_is_a_request_and_gets_the_next_key(self):
        """⛔ Only requests. A board that could mint a decision would put a
        decision record outside the file that owns them."""
        c = await self.signed_in()
        response = await c.post("/api/v1/work", json={
            "title": "kill queries by user", "body": "one at a time today"})
        self.assertEqual(201, response.status_code, response.text[:300])
        item = response.json()
        self.assertEqual("request", item["kind"])
        self.assertTrue(item["key"].startswith("REQ-"))

    async def test_a_status_note_becomes_a_comment(self):
        """What someone wrote when they moved it belongs in the thread."""
        c = await self.signed_in()
        response = await c.put("/api/v1/work/W-1/status", json={
            "status": "in_progress", "note": "starting on this today"})
        self.assertEqual(200, response.status_code, response.text[:300])
        body = (await c.get("/api/v1/work/W-1")).json()
        self.assertTrue(any("starting on this today" in str(e)
                            for e in body["timeline"]))
        self.assertEqual({"comment", "status"},
                         {e["kind"] for e in body["timeline"]})

    async def test_the_markdown_export_is_text_not_json(self):
        c = await self.signed_in()
        response = await c.get("/api/v1/work.md")
        self.assertEqual(200, response.status_code)
        self.assertIn("text/plain", response.headers["content-type"])
        self.assertIn("WORK_BOARD", response.text)

    async def test_an_unreachable_board_says_so_rather_than_erroring(self):
        """⛔ 200 with `available: false`, not a 500.

        The board is a planning surface, not a control plane. A database blip
        should say what happened, not take the endpoint down - a client cannot
        tell a broken server from a feature that was never built.
        """
        config, service, _trino = build_service()
        client = client_for(create_app(config=config, service=service))
        async with client:
            await sign_in(client)
            response = await client.get("/api/v1/work")
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertFalse(body["available"])
        self.assertTrue(body["error"])


@unittest.skipUnless(WEB_DEPS, "web dependencies are not installed")
class FleetApiTest(unittest.IsolatedAsyncioTestCase):
    def build(self):
        from tms.fleet.service import FleetService

        config, service, _trino = build_service()
        fleet = FleetService(
            config=config, snapshots=service.repository,
            audit_guard=service.audit, transport_factory=lambda: None)
        return create_app(config=config, service=service, fleet=fleet)

    async def signed_in(self):
        client = client_for(self.build())
        await client.__aenter__()
        await sign_in(client)
        self.addAsyncCleanup(client.__aexit__, None, None, None)
        return client

    async def test_the_fleet_view_comes_back(self):
        c = await self.signed_in()
        response = await c.get("/api/v1/clusters/prod-a/fleet")
        self.assertEqual(200, response.status_code, response.text[:300])

    async def test_a_shutdown_without_a_reason_is_refused(self):
        """⛔ Draining a worker is irreversible from here."""
        c = await self.signed_in()
        response = await c.post(
            "/api/v1/clusters/prod-a/fleet/nodes/w1/shutdown", json={"reason": "  "})
        self.assertIn(response.status_code, (400, 503), response.text[:300])
        self.assertNotEqual(200, response.status_code)

    async def test_an_unknown_cluster_is_404(self):
        c = await self.signed_in()
        response = await c.get("/api/v1/clusters/nope/fleet")
        self.assertEqual(404, response.status_code)


if __name__ == "__main__":
    unittest.main()
