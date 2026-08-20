"""The work board against a real PostgreSQL (FR-BOARD).

Things a fake cannot tell you:

* whether the append-only grants actually hold - `work_item_comment` and
  `work_item_event` must refuse UPDATE and DELETE to `tms_app`, the same grade
  as the audit log;
* whether the status change and its event row land in one transaction;
* whether the unique key really refuses a duplicate, rather than the in-memory
  repository's `any()` scan doing it;
* whether the lazy connection reconnects after the connection is dropped -
  which is the whole reason the board is allowed to be lazy.

Not named ``test_*`` on purpose: `pytest tests/` must stay infrastructure-free
(same convention as ``smoke_api_postgres.py``).

Run:
    export TMS_SMOKE_DSN='postgresql://tms_admin@localhost:5433/tms_local'
    <venv>/bin/python -m unittest tests.integration.smoke_work_board -v
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

from tms.work.items import BLOCKED, DONE, PLANNED, TASK  # noqa: E402
from tms.work.store import (  # noqa: E402
    BoardUnavailable,
    DuplicateKey,
    PostgresBoardRepository,
)


@unittest.skipUnless(HAVE_PSYCOPG and DSN, "set TMS_SMOKE_DSN to run")
class WorkBoardStoreSmoke(unittest.TestCase):
    def setUp(self):
        self.repository = PostgresBoardRepository(DSN)
        self.key = "SMOKE-" + uuid.uuid4().hex[:8]
        self.addCleanup(self._drop)
        self.repository.create(key=self.key, kind=TASK, title="smoke test",
                               status=PLANNED, created_by="smoke",
                               source_doc="docs/REQUIREMENTS.md")

    def _drop(self):
        self.repository.close()
        # The application has no delete, on purpose. The smoke test cleans up
        # as the owner, through SQL, so the dev board does not fill with runs.
        with psycopg.connect(DSN, autocommit=True) as connection:
            connection.execute("DELETE FROM work_item WHERE key = %s", (self.key,))

    def test_a_duplicate_key_is_refused_by_the_database(self):
        with self.assertRaises(DuplicateKey):
            self.repository.create(key=self.key, kind=TASK, title="again",
                                   status=PLANNED, created_by="smoke")

    def test_a_status_change_writes_its_event(self):
        self.repository.update(self.key, "smoke", status=BLOCKED,
                               blocked_by="waiting on nothing")
        item = self.repository.get(self.key)
        self.assertEqual(BLOCKED, item["status"])
        self.assertEqual(1, len(item["events"]))
        self.assertEqual((PLANNED, BLOCKED, "smoke"),
                         (item["events"][0]["from_status"],
                          item["events"][0]["to_status"],
                          item["events"][0]["actor"]))

    def test_an_unchanged_status_writes_no_event(self):
        # Editing a title must not look like a move on the timeline.
        self.repository.update(self.key, "smoke", title="renamed")
        item = self.repository.get(self.key)
        self.assertEqual("renamed", item["title"])
        self.assertEqual([], item["events"])

    def test_comments_come_back_in_the_order_they_were_written(self):
        for n in range(3):
            self.repository.add_comment(self.key, "smoke", "line {}".format(n))
        bodies = [c["body"] for c in self.repository.get(self.key)["comments"]]
        self.assertEqual(["line 0", "line 1", "line 2"], bodies)

    def test_a_comment_on_a_missing_item_returns_none(self):
        self.assertIsNone(self.repository.add_comment("SMOKE-nope", "smoke", "x"))

    def test_the_connection_comes_back_after_it_is_dropped(self):
        """The reason the board is allowed to connect lazily.

        If a dropped connection were fatal, laziness would only move the
        outage from startup to the first blip and make it permanent.
        """
        self.repository.get(self.key)
        self.repository._connection.close()
        self.assertEqual(self.key, self.repository.get(self.key)["key"])

    def test_a_bad_dsn_reports_unavailable_rather_than_raising_psycopg(self):
        broken = PostgresBoardRepository(
            "postgresql://nobody@127.0.0.1:1/does-not-exist")
        with self.assertRaises(BoardUnavailable):
            broken.list_items()


@unittest.skipUnless(HAVE_PSYCOPG and DSN, "set TMS_SMOKE_DSN to run")
class AppendOnlySmoke(unittest.TestCase):
    """Comments and status events are evidence, at the audit log's grade.

    Checked as `tms_app` - the role tms-api actually connects as. Running this
    as the owner proves nothing: an owner always has full rights on its own
    tables, which is exactly why the two roles exist (deploy.md §3-5).
    """

    APP_DSN = os.environ.get("TMS_SMOKE_APP_DSN")

    @unittest.skipUnless(APP_DSN, "set TMS_SMOKE_APP_DSN to the tms_app role")
    def test_the_application_role_cannot_rewrite_history(self):
        with psycopg.connect(self.APP_DSN, autocommit=True) as connection:
            for statement in (
                "UPDATE work_item_comment SET body = 'tampered'",
                "DELETE FROM work_item_comment",
                "UPDATE work_item_event SET to_status = 'done'",
                "DELETE FROM work_item_event",
            ):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege,
                                       msg=statement):
                    connection.execute(statement)


if __name__ == "__main__":
    unittest.main()
