"""The work board's rules, without an HTTP layer (FR-BOARD).

What is checked here is the part that would quietly rot: that a re-seed never
resets a status someone moved, that only `request` can be raised from the
screen, that a status change and the sentence explaining it end up in one
ordered stream, and that a board nobody can reach says so instead of raising.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.api.errors import Forbidden, InvalidRequest, NotFound, UpstreamUnavailable  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.work import items  # noqa: E402
from tms.work.items import (  # noqa: E402
    BLOCKED,
    DONE,
    IN_PROGRESS,
    NEEDS_DECISION,
    PLANNED,
    REQUEST,
    STATUS_ORDER,
    TASK,
    group_by_status,
    next_request_key,
    summarise,
)
from tms.work.seed import SEED, seed  # noqa: E402
from tms.work.service import BoardService, render_markdown  # noqa: E402
from tms.work.store import BoardUnavailable, DuplicateKey, InMemoryBoardRepository  # noqa: E402

ADMIN = Principal("admin1", ["admin"])
VIEWER = Principal("viewer1", ["viewer"])


class ItemRulesTest(unittest.TestCase):
    def test_needs_decision_is_the_first_column(self):
        """Not alphabetical, not chronological.

        The column that is waiting on the reader comes first, because the whole
        reason to open this screen is to find out whether anything is.
        """
        self.assertEqual(NEEDS_DECISION, STATUS_ORDER[0])

    def test_empty_columns_are_kept(self):
        columns = group_by_status([])
        self.assertEqual(len(STATUS_ORDER), len(columns))
        # "Nothing is waiting on you" is an answer. A missing column is not.
        self.assertEqual(NEEDS_DECISION, columns[0]["status"])
        self.assertEqual([], columns[0]["cards"])

    def test_open_count_excludes_finished_work(self):
        counts = summarise([{"status": DONE}, {"status": BLOCKED},
                            {"status": IN_PROGRESS}])
        self.assertEqual(2, counts["open"])

    def test_request_keys_continue_from_the_highest(self):
        self.assertEqual("REQ-1", next_request_key([]))
        self.assertEqual("REQ-4", next_request_key(["REQ-3", "D-012", "FR-BM-01"]))

    def test_a_malformed_request_key_does_not_stop_numbering(self):
        # A hand-inserted row should not make the next request unnumberable.
        self.assertEqual("REQ-3", next_request_key(["REQ-2", "REQ-oops"]))


class SeedTest(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryBoardRepository()

    def test_seeding_twice_adds_nothing_the_second_time(self):
        first = seed(self.repository)
        self.assertEqual(len(SEED), first)
        self.assertEqual(0, seed(self.repository))

    def test_reseeding_does_not_reset_a_status_someone_moved(self):
        """The failure this exists to prevent.

        A re-seed that overwrote status would silently un-finish finished work,
        and the board would be lying about exactly the thing it is for.
        """
        seed(self.repository)
        self.repository.update("W-1", "admin1", status=DONE)
        seed(self.repository)
        self.assertEqual(DONE, self.repository.get("W-1")["status"])

    def test_every_seeded_item_points_at_a_document(self):
        for key, _kind, _title, _status, _release, _blocked, source in SEED:
            self.assertTrue(source, "{} has no source document".format(key))
            self.assertTrue(os.path.exists(
                os.path.join(os.path.dirname(_HERE), source)),
                "{} points at a document that does not exist: {}".format(key, source))

    def test_blocked_items_say_what_is_blocking_them(self):
        for key, _kind, _title, status, _release, blocked, _source in SEED:
            if status == BLOCKED:
                self.assertTrue(blocked, "{} is blocked by nothing".format(key))


class BoardServiceTest(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryBoardRepository()
        self.board = BoardService(self.repository)
        seed(self.repository)

    def test_any_signed_in_user_can_read_the_board(self):
        data = self.board.board(VIEWER)
        self.assertTrue(data["available"])
        self.assertEqual(len(STATUS_ORDER), len(data["columns"]))

    def test_a_viewer_cannot_write(self):
        with self.assertRaises(Forbidden):
            self.board.raise_request(VIEWER, "give me a button")
        with self.assertRaises(Forbidden):
            self.board.comment(VIEWER, "W-1", "hello")
        with self.assertRaises(Forbidden):
            self.board.set_status(VIEWER, "W-1", DONE)

    def test_a_raised_request_is_a_request_and_starts_planned(self):
        item = self.board.raise_request(ADMIN, "Kill by user, not just by id")
        self.assertEqual(REQUEST, item["kind"])
        # Not needs_decision: nobody is waiting on a person yet, a conversation
        # simply has not happened.
        self.assertEqual(PLANNED, item["status"])
        self.assertEqual("REQ-1", item["key"])
        self.assertEqual("admin1", item["created_by"])

    def test_an_empty_title_is_refused(self):
        with self.assertRaises(InvalidRequest):
            self.board.raise_request(ADMIN, "   ")

    def test_an_empty_comment_is_refused(self):
        with self.assertRaises(InvalidRequest):
            self.board.comment(ADMIN, "W-1", "  \n ")

    def test_a_status_change_records_who_moved_it(self):
        self.board.set_status(ADMIN, "W-1", IN_PROGRESS)
        item = self.board.item(ADMIN, "W-1")
        self.assertEqual(IN_PROGRESS, item["status"])
        self.assertEqual(1, len(item["events"]))
        self.assertEqual(("admin1", PLANNED, IN_PROGRESS),
                         (item["events"][0]["actor"], item["events"][0]["from_status"],
                          item["events"][0]["to_status"]))

    def test_an_unknown_status_is_refused(self):
        with self.assertRaises(InvalidRequest):
            self.board.set_status(ADMIN, "W-1", "almost_done")

    def test_an_unknown_key_is_a_404_not_a_500(self):
        with self.assertRaises(NotFound):
            self.board.item(ADMIN, "REQ-999")
        with self.assertRaises(NotFound):
            self.board.comment(ADMIN, "REQ-999", "hello")

    def test_the_timeline_interleaves_comments_and_moves(self):
        self.board.comment(ADMIN, "W-1", "before")
        self.board.set_status(ADMIN, "W-1", IN_PROGRESS)
        self.board.comment(ADMIN, "W-1", "after")
        timeline = items.timeline(self.board.item(ADMIN, "W-1"))
        self.assertEqual(["comment", "status", "comment"],
                         [entry["kind"] for entry in timeline])

    def test_duplicate_keys_are_refused_by_the_store(self):
        with self.assertRaises(DuplicateKey):
            self.repository.create(key="W-1", kind=TASK, title="again",
                                   status=PLANNED, created_by="admin1")


class UnreachableBoardTest(unittest.TestCase):
    """A board nobody can reach says so. It does not 500 and it does not lie."""

    class Broken:
        def list_items(self, kind=None, status=None):
            raise BoardUnavailable("connection refused")

        def get(self, key):
            raise BoardUnavailable("connection refused")

        def next_key(self):
            raise BoardUnavailable("connection refused")

        def add_comment(self, key, author, body):
            raise BoardUnavailable("connection refused")

        def update(self, key, actor, **fields):
            raise BoardUnavailable("connection refused")

    def setUp(self):
        self.board = BoardService(self.Broken())

    def test_reading_the_board_reports_unavailable_rather_than_raising(self):
        data = self.board.board(ADMIN)
        self.assertFalse(data["available"])
        self.assertIn("connection refused", data["error"])
        # Columns are still shaped, so the template has nothing to guess about.
        self.assertEqual([], data["columns"])

    def test_every_other_call_becomes_a_503(self):
        for call in (lambda: self.board.item(ADMIN, "W-1"),
                     lambda: self.board.raise_request(ADMIN, "something"),
                     lambda: self.board.comment(ADMIN, "W-1", "hello"),
                     lambda: self.board.set_status(ADMIN, "W-1", DONE),
                     lambda: self.board.export_markdown(ADMIN)):
            with self.assertRaises(UpstreamUnavailable):
                call()


class MarkdownExportTest(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryBoardRepository()
        seed(self.repository)
        self.text = render_markdown(self.repository.list_items())

    def test_it_says_it_is_generated(self):
        # Without this line someone edits the file, commits, and loses it on
        # the next export - and concludes the export is broken.
        self.assertIn("tms-work-export", self.text.splitlines()[2])

    def test_it_says_the_document_wins(self):
        self.assertIn("문서가 이긴다", self.text)

    def test_every_item_appears_exactly_once(self):
        for key, _kind, _title, _status, _release, _blocked, _source in SEED:
            self.assertEqual(1, self.text.count("`{}`".format(key)),
                             "{} does not appear exactly once".format(key))

    def test_the_blocker_survives_the_export(self):
        # The export is read by whoever is outside the network. If it drops the
        # blocker they cannot tell a blocked item from an idle one.
        self.assertIn("Prometheus 미구축", self.text)


class VocabularyTest(unittest.TestCase):
    """What the statuses and kinds *are* is server knowledge.

    ⛔ The console is handed these rather than writing them down. A
    hand-written copy in a screen is a second definition, and the two drift
    the first time one is added - which is what happened to the release plan
    and BACKLOG.md.
    """

    def test_every_status_comes_with_its_label_and_meaning(self):
        choices = items.statuses()
        self.assertEqual(list(STATUS_ORDER), [c["value"] for c in choices])
        for choice in choices:
            self.assertTrue(choice["label"])
            self.assertTrue(choice["meaning"])

    def test_every_kind_comes_with_its_label(self):
        listed = items.kinds()
        self.assertEqual(sorted(items.KIND_LABELS), sorted(k["value"] for k in listed))
        for kind in listed:
            self.assertTrue(kind["label"])

    def test_the_board_carries_the_kinds_its_filter_needs(self):
        repository = InMemoryBoardRepository()
        seed(repository)
        board = BoardService(repository).board(ADMIN)
        self.assertEqual(len(SEED), sum(len(c["cards"]) for c in board["columns"]))
        self.assertEqual(items.kinds(), board["kinds"])


if __name__ == "__main__":
    unittest.main()
