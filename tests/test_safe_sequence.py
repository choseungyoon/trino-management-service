"""Tests for the safe restart sequence (FR-CO-02).

CLAUDE.md rule 5: "이 시퀀스를 건너뛰는 경로는 구현하지 않는다." Most of these
tests exist to prove there is no such path — restarting a coordinator kills
every query on it, so the ordering *is* the feature.
"""

import os
import pathlib
import re
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.ops.sequence import (  # noqa: E402
    ALLOWED_LEVELS,
    checklist,
    LEVEL_OUTPUT,
    ABORTED,
    ABORTING,
    COMPLETED,
    DRAINED,
    DRAINING,
    PENDING,
    RESTARTING,
    VERIFYING,
    RestartSequence,
    SequenceError,
    StepBlocked,
)


def seq(**kwargs):
    return RestartSequence(cluster="prod-a", reason="rolling config change",
                           actor="operator1", **kwargs)


def drained_sequence():
    s = seq()
    s.begin()
    s.observe(running_queries=0, health_state="GOOD")
    s.confirm_drained()
    return s


class ReasonTest(unittest.TestCase):
    def test_a_reason_is_required(self):
        for bad in ("", "   ", None):
            with self.assertRaises(SequenceError):
                RestartSequence("prod-a", bad, "operator1")


class OrderingTest(unittest.TestCase):
    """No step may be skipped."""

    def test_happy_path(self):
        s = seq()
        self.assertEqual(PENDING, s.state)
        s.begin()
        self.assertEqual(DRAINING, s.state)
        s.observe(running_queries=0, health_state="GOOD")
        s.confirm_drained()
        self.assertEqual(DRAINED, s.state)
        s.mark_restarting()
        s.mark_restarted()
        self.assertEqual(VERIFYING, s.state)
        s.observe(running_queries=0, health_state="GOOD")
        s.confirm_healthy()
        s.complete()
        self.assertEqual(COMPLETED, s.state)

    def test_cannot_restart_before_deactivating(self):
        with self.assertRaises(SequenceError):
            seq().mark_restarting()

    def test_cannot_restart_while_queries_are_running(self):
        """The whole point. A restart here kills them."""
        s = seq()
        s.begin()
        s.observe(running_queries=3)
        with self.assertRaises(StepBlocked) as caught:
            s.confirm_drained()
        self.assertIn("3 queries are still running", str(caught.exception))
        with self.assertRaises(SequenceError):
            s.mark_restarting()

    def test_cannot_confirm_drain_without_an_observation(self):
        """Absence of data is not evidence of an empty cluster."""
        s = seq()
        s.begin()
        with self.assertRaises(StepBlocked):
            s.confirm_drained()

    def test_cannot_complete_before_restarting(self):
        s = drained_sequence()
        with self.assertRaises(SequenceError):
            s.complete()

    def test_cannot_reactivate_while_health_is_bad(self):
        s = drained_sequence()
        s.mark_restarting()
        s.mark_restarted()
        s.observe(running_queries=0, health_state="BAD")
        with self.assertRaises(StepBlocked):
            s.confirm_healthy()
        with self.assertRaises(StepBlocked):
            s.complete()

    def test_unknown_health_is_not_good_enough(self):
        """UNKNOWN must never be treated as healthy — same rule as the health
        roll-up."""
        s = drained_sequence()
        s.mark_restarting()
        s.mark_restarted()
        s.observe(running_queries=0, health_state="UNKNOWN")
        with self.assertRaises(StepBlocked):
            s.complete()

    def test_a_finished_sequence_cannot_be_restarted(self):
        s = seq()
        s.begin()
        s.begin_abort()
        s.finish_abort()
        with self.assertRaises(SequenceError):
            s.begin_abort()


class DrainTest(unittest.TestCase):
    def test_observing_zero_advances_automatically(self):
        s = seq()
        s.begin()
        s.observe(running_queries=0)
        self.assertEqual(DRAINED, s.state)

    def test_forcing_requires_its_own_reason(self):
        s = seq()
        s.begin()
        s.observe(running_queries=4)
        with self.assertRaises(SequenceError):
            s.force_drained("")

    def test_forcing_records_how_many_queries_were_killed(self):
        """Afterwards this must not look like a routine drain."""
        s = seq()
        s.begin()
        s.observe(running_queries=4)
        s.force_drained("stuck query, owner unreachable, incident 4821")
        self.assertEqual(DRAINED, s.state)
        entry = s.history[-1]
        self.assertIn("FORCED", entry["message"])
        self.assertIn("4", entry["message"])
        self.assertIn("incident 4821", entry["message"])
        self.assertEqual("warn", entry["level"], "a forced drain is not routine")


class AbortTest(unittest.TestCase):
    """Abort means "put the traffic back", not "stop doing things"."""

    def test_abort_is_not_terminal_until_traffic_is_restored(self):
        s = seq()
        s.begin()
        s.begin_abort()
        self.assertEqual(ABORTING, s.state)
        self.assertTrue(s.traffic_stopped,
                        "still deactivated until reactivation completes")
        s.finish_abort()
        self.assertEqual(ABORTED, s.state)
        self.assertFalse(s.traffic_stopped)

    def test_abort_is_possible_from_every_active_state(self):
        for build in (
            lambda: seq(),
            lambda: (lambda s: (s.begin(), s)[1])(seq()),
            drained_sequence,
        ):
            s = build()
            s.begin_abort()
            self.assertEqual(ABORTING, s.state)


class TrafficStateTest(unittest.TestCase):
    def test_traffic_is_stopped_for_the_whole_middle_of_the_sequence(self):
        s = seq()
        self.assertFalse(s.traffic_stopped)
        s.begin()
        s.observe(running_queries=0, health_state="GOOD")
        self.assertTrue(s.traffic_stopped)
        s.mark_restarting()
        self.assertTrue(s.traffic_stopped)
        s.mark_restarted()
        self.assertTrue(s.traffic_stopped)
        s.confirm_healthy()
        s.complete()
        self.assertFalse(s.traffic_stopped, "traffic is back once completed")


class OperatorHandoffTest(unittest.TestCase):
    """TMS cannot restart a coordinator; it must say so plainly."""

    def test_waiting_on_a_human_is_explicit(self):
        s = seq()
        s.begin()
        self.assertFalse(s.needs_operator_action, "TMS is watching the drain")
        s.observe(running_queries=0, health_state="GOOD")
        self.assertTrue(s.needs_operator_action, "the restart is theirs to do")
        s.mark_restarting()
        self.assertTrue(s.needs_operator_action)
        s.mark_restarted()
        self.assertFalse(s.needs_operator_action, "TMS checks health itself")


class ChecklistTest(unittest.TestCase):
    def test_steps_mark_progress(self):
        s = drained_sequence()
        statuses = {state: status for state, _, status in s.steps()}
        self.assertEqual("done", statuses[DRAINING])
        self.assertEqual("current", statuses[DRAINED])
        self.assertEqual("pending", statuses[RESTARTING])

    def test_aborted_sequence_is_not_shown_as_progressing(self):
        s = seq()
        s.begin()
        s.begin_abort()
        self.assertTrue(all(status == "aborted" for _, _, status in s.steps()))


class ProgressLogTest(unittest.TestCase):
    """The log is the screen — an operator watches the restart happen."""

    def test_every_entry_is_timestamped_and_stateful(self):
        s = seq()
        s.begin()
        entry = s.history[-1]
        self.assertIn("at", entry)
        self.assertEqual(DRAINING, entry["state"])
        self.assertEqual("info", entry["level"])

    def test_the_drain_reports_progress_without_changing_state(self):
        """Otherwise the screen sits still while the queue empties."""
        s = seq()
        s.begin()
        s.observe(running_queries=3)
        s.observe(running_queries=1)
        self.assertEqual(DRAINING, s.state)
        messages = [h["message"] for h in s.history]
        self.assertIn("Waiting for 3 running queries to finish.", messages)
        self.assertIn("Waiting for 1 running query to finish.", messages)

    def test_the_log_reads_as_a_sequence_of_operations(self):
        s = seq()
        s.begin()
        s.observe(running_queries=0, health_state="GOOD")
        s.mark_restarting()
        s.mark_restarted()
        s.confirm_healthy()
        s.complete()
        messages = [h["message"] for h in s.history]
        self.assertEqual("Blocking new queries to prod-a in the Gateway.", messages[0])
        self.assertIn("empty", messages[1])
        self.assertIn("Bringing prod-a down", messages[2])
        self.assertIn("Checking health", messages[3])
        self.assertIn("Health is GOOD", messages[4])
        self.assertIn("back in rotation", messages[-1])

    def test_abort_is_logged_as_a_warning(self):
        s = seq()
        s.begin()
        s.begin_abort()
        self.assertEqual("warn", s.history[-1]["level"])
        self.assertIn("Restoring traffic", s.history[-1]["message"])


class ChecklistTest(unittest.TestCase):
    def test_the_preview_and_the_live_checklist_are_the_same_list(self):
        """The start page shows the procedure before anyone commits to it, and
        the live view shows progress through it. Two hand-written copies would
        drift, and this is the one screen that must not lie about the order."""
        s = seq()
        live = [label for _state, label, _status in s.steps()]
        preview = [row["label"] for row in checklist()]
        self.assertEqual(preview, live)

    def test_the_checklist_does_not_say_who_performs_the_restart(self):
        """Configuration decides that (manual operator vs Ansible), so a
        checklist that hard-codes either is wrong half the time."""
        labels = " ".join(row["label"] for row in checklist()).lower()
        for word in ("operator", "ansible", "playbook"):
            self.assertNotIn(word, labels)

    def test_a_finished_sequence_has_no_step_in_progress(self):
        """`current` is what the UI animates. A completed restart that still
        pulses reads as "something is happening" when nothing is."""
        s = seq()
        s.begin()
        s.observe(0)
        s.mark_restarting()
        s.mark_restarted()
        s.health_state = "GOOD"
        s.complete()
        statuses = [status for _state, _label, status in s.steps()]
        self.assertEqual(["done"] * 6, statuses)

    def test_exactly_one_step_is_current_while_it_runs(self):
        s = seq()
        s.begin()
        statuses = [status for _state, _label, status in s.steps()]
        self.assertEqual(1, statuses.count("current"), statuses)

    def test_the_state_label_still_describes_the_state(self):
        """`label` in the payload feeds the banner, which answers "what is
        happening", not "what does this step do"."""
        s = seq()
        s.begin()
        self.assertIn("Draining", s.as_dict()["label"])


class LogLevelTest(unittest.TestCase):
    """The code and the database must agree on what a level may be.

    Same guard as the audit action catalogue, and for the same reason: a level
    the schema rejects fails the whole save, losing the line being recorded -
    in the middle of a restart, which is the worst possible moment to discover
    it. The effective constraint is the last definition across all migrations,
    because 007 replaces the one 004 wrote.
    """

    def _effective_constraint(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pattern = re.compile(
            r"CONSTRAINT\s+restart_sequence_event_level_valid\s+CHECK\s*\((.*?)\)\s*;",
            re.S | re.I)
        found = None
        for path in sorted(pathlib.Path(repo_root, "migrations").glob("*.sql")):
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                found = (match.group(1), path.name)
        return found

    def test_every_allowed_level_is_permitted_by_the_schema(self):
        effective = self._effective_constraint()
        self.assertIsNotNone(effective, "the level constraint is not defined anywhere")
        definition, source = effective
        for level in ALLOWED_LEVELS:
            self.assertIn("'{}'".format(level), definition,
                          "{} is in ALLOWED_LEVELS but the CHECK constraint in {} "
                          "would reject it".format(level, source))

    def test_an_unknown_level_is_refused_in_code(self):
        """Refused here rather than by the database, which would fail the save
        and take the line with it."""
        s = seq()
        with self.assertRaises(SequenceError):
            s.log("something", level="debug")

    def test_playbook_output_has_its_own_level(self):
        """Verbatim text from another program is not TMS asserting something,
        and the UI renders it as a terminal rather than as prose."""
        s = seq()
        s.log("TASK [restart coordinator] ***", level=LEVEL_OUTPUT)
        self.assertEqual(LEVEL_OUTPUT, s.history[-1]["level"])


if __name__ == "__main__":
    unittest.main()
