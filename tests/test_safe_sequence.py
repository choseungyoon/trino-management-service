"""Tests for the safe restart sequence (FR-CO-02).

CLAUDE.md rule 5: "이 시퀀스를 건너뛰는 경로는 구현하지 않는다." Most of these
tests exist to prove there is no such path — restarting a coordinator kills
every query on it, so the ordering *is* the feature.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.ops.sequence import (  # noqa: E402
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
        note = s.history[-1]["note"]
        self.assertIn("FORCED", note)
        self.assertIn("4", note)
        self.assertIn("incident 4821", note)


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


if __name__ == "__main__":
    unittest.main()
