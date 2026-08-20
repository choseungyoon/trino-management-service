"""The work board screens, driven through ASGI (FR-BOARD).

The rules have their own tests. What is checked here is the wiring an
administrator touches: that a viewer can read the board but cannot move
anything, that a refused request hands back what was typed, that the status
form's optional note lands as a comment, and that /work.md serves the same
bytes tms-work-export writes.
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

from tms.api.main import create_app  # noqa: E402
from tms.work.items import BLOCKED, DONE, IN_PROGRESS  # noqa: E402
from tms.work.seed import seed  # noqa: E402
from tms.work.service import BoardService, render_markdown  # noqa: E402
from tms.work.store import BoardUnavailable, InMemoryBoardRepository  # noqa: E402

from test_web_routes import PASSWORD, build_service, client_for, sign_in  # noqa: E402


class Unreachable:
    """Every call fails, the way a database that is down behaves."""

    def list_items(self, kind=None, status=None):
        raise BoardUnavailable("connection refused")

    def get(self, key):
        raise BoardUnavailable("connection refused")


@unittest.skipUnless(WEB_DEPS, "fastapi/httpx/jinja2/python-multipart not installed")
class WorkBoardScreenTest(unittest.IsolatedAsyncioTestCase):
    def build(self, roles=("admin",), repository=None):
        config, service, _trino = build_service(roles=roles)
        self.repository = repository if repository is not None else InMemoryBoardRepository()
        if repository is None:
            seed(self.repository)
        app = create_app(config=config, service=service,
                         board=BoardService(self.repository))
        return app

    def setUp(self):
        self.app = self.build()

    def client(self, app=None):
        return client_for(app or self.app)

    # ------------------------------------------------------------- reading

    async def test_the_board_is_behind_the_login(self):
        async with self.client() as c:
            response = await c.get("/work")
        self.assertEqual(303, response.status_code)
        self.assertIn("/login", response.headers["location"])

    async def test_the_board_shows_every_column_including_the_empty_ones(self):
        async with self.client() as c:
            await sign_in(c)
            response = await c.get("/work")
        self.assertEqual(200, response.status_code)
        for label in ("Needs a decision", "Blocked", "In progress", "Planned",
                      "Done", "Dropped"):
            self.assertIn(label, response.text)

    async def test_the_board_says_the_document_wins(self):
        """The one sentence that keeps this screen from becoming a second truth."""
        async with self.client() as c:
            await sign_in(c)
            response = await c.get("/work")
        self.assertIn("문서가 이긴다", response.text)

    async def test_a_blocked_card_carries_its_blocker(self):
        async with self.client() as c:
            await sign_in(c)
            response = await c.get("/work")
        self.assertIn("Prometheus 미구축", response.text)

    async def test_the_kind_filter_narrows_the_board(self):
        async with self.client() as c:
            await sign_in(c)
            response = await c.get("/work?kind=task")
        self.assertEqual(200, response.status_code)
        self.assertIn("NFR-PERF-03", response.text)
        self.assertNotIn("FR-BM-01", response.text)

    async def test_an_unknown_item_is_a_404_page_not_a_traceback(self):
        async with self.client() as c:
            await sign_in(c)
            response = await c.get("/work/REQ-999")
        self.assertEqual(404, response.status_code)

    async def test_an_unreachable_board_says_so_and_still_renders(self):
        app = self.build(repository=Unreachable())
        async with self.client(app) as c:
            await sign_in(c)
            response = await c.get("/work")
        self.assertEqual(200, response.status_code)
        self.assertIn("보드를 읽을 수 없다", response.text)
        self.assertIn("connection refused", response.text)

    # ------------------------------------------------------------- writing

    async def test_an_admin_can_raise_a_request(self):
        async with self.client() as c:
            await sign_in(c)
            response = await c.post("/work", data={"title": "Kill by user",
                                                   "body": "지금은 id 로만 된다"})
        self.assertEqual(303, response.status_code)
        self.assertEqual("/work/REQ-1", response.headers["location"])
        self.assertEqual("Kill by user", self.repository.get("REQ-1")["title"])

    async def test_a_refused_request_hands_back_what_was_typed(self):
        """Losing someone's paragraph because the title was blank teaches them
        to write it somewhere else first."""
        async with self.client() as c:
            await sign_in(c)
            response = await c.post("/work", data={"title": "  ",
                                                   "body": "긴 설명을 적었다"})
        self.assertEqual(400, response.status_code)
        self.assertIn("긴 설명을 적었다", response.text)

    async def test_a_viewer_sees_the_board_but_no_form(self):
        app = self.build(roles=("viewer",))
        async with self.client(app) as c:
            await sign_in(c)
            board = await c.get("/work")
            posted = await c.post("/work", data={"title": "give me a button"})
        self.assertEqual(200, board.status_code)
        self.assertNotIn("요청 올리기", board.text)
        # Refused with the board still rendered and the reason on it, rather
        # than a bare 403 that leaves the reader on an empty page.
        self.assertEqual(403, posted.status_code)
        self.assertIn("administrators", posted.text)
        self.assertIsNone(self.repository.get("REQ-1"))

    async def test_a_status_change_with_a_note_records_both(self):
        async with self.client() as c:
            await sign_in(c)
            response = await c.post("/work/W-1/status",
                                    data={"status": IN_PROGRESS,
                                          "note": "피크 시간대에 돌리기로 했다"})
        self.assertEqual(303, response.status_code)
        item = self.repository.get("W-1")
        self.assertEqual(IN_PROGRESS, item["status"])
        self.assertEqual(1, len(item["events"]))
        self.assertEqual("피크 시간대에 돌리기로 했다", item["comments"][0]["body"])

    async def test_a_rejected_status_leaves_the_item_alone(self):
        async with self.client() as c:
            await sign_in(c)
            response = await c.post("/work/W-1/status", data={"status": "almost"})
        self.assertEqual(303, response.status_code)
        self.assertNotEqual("almost", self.repository.get("W-1")["status"])

    async def test_a_comment_lands_on_the_item(self):
        async with self.client() as c:
            await sign_in(c)
            response = await c.post("/work/D-2/comment", data={"body": "아직 결정 못 했다"})
        self.assertEqual(303, response.status_code)
        self.assertEqual("아직 결정 못 했다",
                         self.repository.get("D-2")["comments"][0]["body"])

    async def test_an_empty_comment_is_refused_without_a_500(self):
        async with self.client() as c:
            await sign_in(c)
            response = await c.post("/work/D-2/comment", data={"body": "   "})
        self.assertEqual(303, response.status_code)
        self.assertEqual([], self.repository.get("D-2")["comments"])

    async def test_the_detail_page_shows_the_timeline_in_order(self):
        async with self.client() as c:
            await sign_in(c)
            await c.post("/work/W-1/comment", data={"body": "먼저 남긴 댓글"})
            await c.post("/work/W-1/status", data={"status": BLOCKED})
            response = await c.get("/work/W-1")
        self.assertEqual(200, response.status_code)
        self.assertLess(response.text.index("먼저 남긴 댓글"),
                        response.text.index("Blocked</strong>"))

    # ------------------------------------------------------------ exporting

    async def test_work_md_serves_what_the_export_command_writes(self):
        async with self.client() as c:
            await sign_in(c)
            response = await c.get("/work.md")
        self.assertEqual(200, response.status_code)
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(render_markdown(self.repository.list_items()), response.text)

    async def test_the_export_follows_the_board(self):
        async with self.client() as c:
            await sign_in(c)
            await c.post("/work/W-1/status", data={"status": DONE})
            response = await c.get("/work.md")
        # The exported file is what somebody outside the network reads. If it
        # lags the board it is worse than no file.
        self.assertIn(render_markdown(self.repository.list_items()), response.text)


if __name__ == "__main__":
    unittest.main()
