"""Tests for Ansible-backed restart execution.

The platform team chose (2026-08-08) to have TMS run `ansible-playbook` from
the TMS host, which means the TMS host holds SSH access to every Trino node.
These tests are mostly about the blast radius of that decision: nothing a
request carries may influence what gets run or where.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.ops.ansible import (  # noqa: E402
    UNKNOWN,
    AnsibleError,
    AnsibleRestartExecutor,
    redact,
)
from tms.ops.executor import FAILED, RUNNING, SUCCEEDED  # noqa: E402

HOSTS = {"prod-a": "trino-a-coord", "prod-b": "trino-b-coord"}


class FakeRunner:
    def __init__(self, rc=0, timed_out=False, output="", error=None):
        self.result = {"rc": rc, "output": output}
        if timed_out:
            self.result = {"rc": None, "timed_out": True, "output": ""}
        if error:
            self.result = {"rc": None, "error": error, "output": ""}
        self.commands = []

    def __call__(self, command, timeout):
        self.commands.append(list(command))
        return dict(self.result)


def executor(runner=None, **kwargs):
    return AnsibleRestartExecutor(
        playbook="/opt/tms/ansible/restart.yml", cluster_hosts=HOSTS,
        inventory=None, runner=runner or FakeRunner(), **kwargs)


def wait(ex, sequence_id):
    run = ex._runs[sequence_id]
    run["thread"].join(timeout=5)
    return ex.status("prod-a", sequence_id)


class ConfigurationTest(unittest.TestCase):
    def test_playbook_must_be_absolute(self):
        with self.assertRaises(AnsibleError):
            AnsibleRestartExecutor(playbook="restart.yml", cluster_hosts=HOSTS,
                                   runner=FakeRunner())

    def test_playbook_is_configuration_not_input(self):
        """There is no argument anywhere that selects a playbook."""
        ex = executor()
        self.assertEqual("/opt/tms/ansible/restart.yml", ex.build_command("prod-a")[1])


class TargetingTest(unittest.TestCase):
    """Nothing a request carries may reach the command line."""

    def test_unknown_cluster_is_refused(self):
        with self.assertRaises(AnsibleError) as caught:
            executor().build_command("not-a-cluster")
        self.assertIn("not in config.yaml", str(caught.exception))

    def test_the_configured_host_is_used_not_the_supplied_name(self):
        command = executor().build_command("prod-a")
        self.assertIn("trino-a-coord", command)
        self.assertNotIn("prod-a", command)

    def test_injection_attempts_are_refused_before_anything_runs(self):
        hostile = [
            "prod-a; rm -rf /",
            "prod-a && curl evil.invalid",
            "../../etc/passwd",
            "prod-a\nprod-b",
            "$(whoami)",
            "`id`",
            "*",
        ]
        runner = FakeRunner()
        ex = executor(runner)
        for name in hostile:
            with self.assertRaises(AnsibleError, msg=name):
                ex.build_command(name)
        self.assertEqual([], runner.commands, "nothing was executed")

    def test_a_malformed_configured_host_is_also_refused(self):
        """Defence in depth: bad configuration must not become a command."""
        ex = AnsibleRestartExecutor(playbook="/opt/tms/p.yml",
                                    cluster_hosts={"prod-a": "host; rm -rf /"},
                                    runner=FakeRunner())
        with self.assertRaises(AnsibleError):
            ex.build_command("prod-a")

    def test_command_is_a_list_so_there_is_no_shell_to_escape(self):
        command = executor().build_command("prod-a")
        self.assertIsInstance(command, list)
        self.assertTrue(all(isinstance(part, str) for part in command))


class LifecycleTest(unittest.TestCase):
    def test_success(self):
        ex = executor(FakeRunner(rc=0))
        self.assertEqual(RUNNING, ex.start("prod-a", "seq-1"))
        self.assertEqual(SUCCEEDED, wait(ex, "seq-1"))

    def test_non_zero_exit_is_a_failure(self):
        ex = executor(FakeRunner(rc=2))
        ex.start("prod-a", "seq-1")
        self.assertEqual(FAILED, wait(ex, "seq-1"))
        self.assertIn("exited 2", ex.result("seq-1")["error"])

    def test_a_hanging_playbook_fails_rather_than_stranding_the_cluster(self):
        ex = executor(FakeRunner(timed_out=True), timeout_seconds=1)
        ex.start("prod-a", "seq-1")
        self.assertEqual(FAILED, wait(ex, "seq-1"))
        self.assertIn("did not finish", ex.result("seq-1")["error"])

    def test_missing_binary_is_a_failure_not_a_crash(self):
        ex = executor(FakeRunner(error="No such file or directory"))
        ex.start("prod-a", "seq-1")
        self.assertEqual(FAILED, wait(ex, "seq-1"))

    def test_starting_twice_does_not_run_twice(self):
        """The sequence may be resumed; a second restart would be an incident."""
        runner = FakeRunner(rc=0)
        ex = executor(runner)
        ex.start("prod-a", "seq-1")
        wait(ex, "seq-1")
        ex.start("prod-a", "seq-1")
        self.assertEqual(1, len(runner.commands))

    def test_unseen_sequence_is_unknown_not_success(self):
        """After a TMS restart the run is not observable. Guessing success here
        would restore traffic to a cluster that may not be back."""
        self.assertEqual(UNKNOWN, executor().status("prod-a", "never-started"))


class RedactionTest(unittest.TestCase):
    def test_obvious_secrets_are_masked(self):
        masked = redact("ok\npassword: hunter2\napi_key=abcd1234\n")
        self.assertNotIn("hunter2", masked)
        self.assertNotIn("abcd1234", masked)
        self.assertIn("password", masked)

    def test_ordinary_output_survives(self):
        text = "PLAY RECAP\ntrino-a-coord : ok=5 changed=1 failed=0"
        self.assertEqual(text, redact(text))


if __name__ == "__main__":
    unittest.main()
