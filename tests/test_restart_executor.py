"""Tests for the restart execution seam (FR-CO-02 step 4)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.core.config import AnsibleConfig, ClusterOpsConfig  # noqa: E402
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
    """Which executor an administrator gets, and what happens when they
    misconfigure the automated one."""

    class _Config:
        def __init__(self, cluster_ops=None):
            self.cluster_ops = cluster_ops

    @staticmethod
    def _ops(mode, **ansible):
        return ClusterOpsConfig(restart_mode=mode, ansible=AnsibleConfig(**ansible))

    def test_default_is_manual(self):
        """Automating this hands TMS SSH access to every Trino node. It never
        happens by default, and never as a side effect of installing Ansible."""
        self.assertIsInstance(build_executor(self._Config()), ManualExecutor)
        self.assertIsInstance(
            build_executor(self._Config(self._ops("manual"))), ManualExecutor)

    def test_ansible_mode_builds_the_ansible_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            playbook = os.path.join(tmp, "restart.yml")
            inventory = os.path.join(tmp, "cluster1.ini")
            for path in (playbook, inventory):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("---\n")
            executor = build_executor(self._Config(self._ops(
                "ansible", playbook=playbook, binary=sys.executable,
                state_dir=tmp, inventories={"prod-a": inventory})))
        self.assertEqual("ansible", executor.name)
        self.assertTrue(executor.automated)

    def test_a_missing_ansible_binary_falls_back_at_startup(self):
        """Ansible frequently lives on a separate control node, not on the TMS
        host. Finding that out mid-restart means finding out with the cluster
        already drained and out of rotation - so it is a startup decision."""
        with tempfile.TemporaryDirectory() as tmp:
            playbook = os.path.join(tmp, "restart.yml")
            inventory = os.path.join(tmp, "cluster1.ini")
            for path in (playbook, inventory):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("---\n")
            executor = build_executor(self._Config(self._ops(
                "ansible", playbook=playbook, state_dir=tmp,
                binary="/nonexistent/ansible-playbook",
                inventories={"prod-a": inventory})))
        self.assertIsInstance(executor, ManualExecutor)

    def test_an_unwritable_state_dir_falls_back_at_startup(self):
        """Ansible aborts at import time without a writable HOME (exit 5,
        measured on ansible-core 2.21). The tms-api unit sets ProtectHome=true,
        so this is the default outcome unless StateDirectory=trino-management-service is set."""
        with tempfile.TemporaryDirectory() as tmp:
            playbook = os.path.join(tmp, "restart.yml")
            inventory = os.path.join(tmp, "cluster1.ini")
            for path in (playbook, inventory):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("---\n")
            executor = build_executor(self._Config(self._ops(
                "ansible", playbook=playbook, binary=sys.executable,
                state_dir="/nonexistent/tms-state",
                inventories={"prod-a": inventory})))
        self.assertIsInstance(executor, ManualExecutor)

    def test_a_misconfigured_ansible_falls_back_to_manual(self):
        """A missing playbook must not become "TMS cannot restart anything" in
        the middle of an incident. The operator can still drive the sequence by
        hand, which is the part that prevents the outage."""
        executor = build_executor(self._Config(self._ops(
            "ansible", playbook="/nonexistent/restart.yml",
            inventories={"prod-a": "/nonexistent/cluster1.ini"})))
        self.assertIsInstance(executor, ManualExecutor)

    def test_interface_methods_are_abstract(self):
        base = RestartExecutor()
        for call in (lambda: base.start("c", "s"), lambda: base.status("c", "s")):
            with self.assertRaises(NotImplementedError):
                call()
