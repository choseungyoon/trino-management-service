"""Tests for the restart execution seam (FR-CO-02 step 4)."""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.ops.executor import (  # noqa: E402
    PENDING_OPERATOR,
    SUCCEEDED,
    ManualExecutor,
    RestartExecutor,
    build_executor,
)


class ManualExecutorTest(unittest.TestCase):
    def test_starts_by_waiting_for_a_human(self):
        self.assertEqual(PENDING_OPERATOR, ManualExecutor().start("prod-a", "seq-1"))

    def test_status_flips_only_once_reported(self):
        ex = ManualExecutor()
        self.assertEqual(PENDING_OPERATOR, ex.status("prod-a", "seq-1"))
        ex.report_done("seq-1")
        self.assertEqual(SUCCEEDED, ex.status("prod-a", "seq-1"))

    def test_one_sequence_reporting_does_not_complete_another(self):
        ex = ManualExecutor()
        ex.report_done("seq-1")
        self.assertEqual(PENDING_OPERATOR, ex.status("prod-a", "seq-2"))

    def test_it_announces_that_it_is_not_automated(self):
        """The UI uses this to decide whether to tell someone to act."""
        ex = ManualExecutor()
        self.assertFalse(ex.automated)
        self.assertFalse(ex.describe("prod-a")["automated"])
        self.assertIn("prod-a", ex.describe("prod-a")["title"])


class BuildTest(unittest.TestCase):
    class _Ops:
        def __init__(self, mode):
            self.restart_mode = mode

    class _Config:
        def __init__(self, mode=None):
            self.cluster_ops = BuildTest._Ops(mode) if mode else None

    def test_default_is_manual(self):
        self.assertIsInstance(build_executor(self._Config()), ManualExecutor)

    def test_unimplemented_mode_falls_back_to_manual_rather_than_failing(self):
        """An unknown mode must not leave the sequence with no way to restart -
        that would strand a deactivated cluster."""
        self.assertIsInstance(build_executor(self._Config("ansible")), ManualExecutor)

    def test_interface_methods_are_abstract(self):
        base = RestartExecutor()
        for call in (lambda: base.start("c", "s"), lambda: base.status("c", "s")):
            with self.assertRaises(NotImplementedError):
                call()
