"""Ansible-backed restart execution (FR-CO-02 step 4).

The platform team runs config/catalog deployment and coordinator/worker
restarts through Ansible already, and chose (2026-08-08) to have TMS invoke
`ansible-playbook` directly from the TMS host.

That choice means the TMS host holds SSH access to every Trino node, so this
module's job is to make sure the only thing that access can ever be used for is
the specific playbook an administrator configured. Everything below exists for
that reason:

* **The playbook is configuration, never input.** It is an absolute path from
  `config.yaml`, checked to exist at construction. Nothing a user types can
  select, alter or add a playbook.
* **No shell, ever.** `subprocess` is called with an argument list. There is no
  string a cluster name could break out of.
* **The cluster must be one TMS already knows.** The name is matched against
  the configured clusters before it reaches a command line, and what is passed
  to Ansible is the configured inventory *path* for that cluster, not the
  string the request carried.

The platform team keeps one inventory file per cluster (`cluster1.ini`,
`cluster2.ini`, ...) holding that cluster's coordinator and worker addresses,
and the playbook restarts workers in sequence and the coordinator last. So the
target is chosen by picking an inventory file, not by filtering a shared one -
there is no host name on the command line at all, which removes a whole class
of targeting mistake.
* **Timeouts.** A playbook that hangs must fail the step, not strand a
  deactivated cluster forever.
* **Output streams.** Lines are surfaced as Ansible produces them, so the
  operator watches the restart happen instead of staring at a blank panel for
  several minutes and having to guess whether it is working or stuck.
* **Never claim an unverified success.** If TMS restarts while a playbook is
  running, status reports "unknown" and points at the log. Reporting success
  it did not observe would restore traffic to a cluster that may not be back.

Output is captured for the audit trail with obvious secrets redacted; Ansible
output is not guaranteed clean, so treat the log as sensitive regardless.

Python 3.9 compatible.
"""

import logging
import os
import re
import shutil
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional

from tms.ops.process import redact, stream_command
from tms.ops.executor import (
    FAILED,
    RUNNING,
    SUCCEEDED,
    UNKNOWN,
    RestartExecutor,
)

log = logging.getLogger(__name__)

# A conservative name shape. The value is also matched against configured
# clusters, so this is belt-and-braces against anything odd reaching argv.
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

class AnsibleError(Exception):
    """Configuration or invocation problem, raised before anything runs."""


def _writable(path: str) -> bool:
    return bool(path) and os.path.isdir(path) and os.access(path, os.W_OK)


# Ansible's own default (`-C -o ControlMaster=auto -o ControlPersist=60s`).
# Repeated here because setting ANSIBLE_SSH_ARGS replaces the default outright
# rather than adding to it, and losing connection multiplexing would open a
# fresh SSH session per task on every node.
_DEFAULT_SSH_ARGS = ("-C", "-o", "ControlMaster=auto", "-o", "ControlPersist=60s")


def ssh_directory(state_dir: str) -> str:
    return os.path.join(state_dir, ".ssh")


def known_hosts_path(state_dir: str) -> str:
    return os.path.join(ssh_directory(state_dir), "known_hosts")


def ansible_environment(state_dir: str) -> Dict[str, str]:
    """The environment the playbook runs in.

    Inherited, then overridden - a wholly clean environment would drop the
    things SSH and Ansible need to find (PATH, SSH_AUTH_SOCK, ANSIBLE_CONFIG).
    What is pinned is only what the sandbox breaks: everything Ansible wants to
    write under HOME goes to the service's own state directory instead.

    ⛔ Setting HOME is not enough for SSH. OpenSSH expands `~` from the passwd
    entry (`getpwuid`), *not* from `$HOME`, so the client still resolves
    `~/.ssh` to the service account's real home - which `ProtectHome=true` makes
    unreachable. The visible symptom is an Ansible failure that reads like a
    network problem:

        UNREACHABLE! => Failed to connect to the host via ssh:
        Could not create directory '/home/<account>/.ssh' (Permission denied)

    It is not a network problem, and no firewall rule fixes it. The client is
    trying to write `known_hosts` before it has connected to anything. So the
    known_hosts file is pointed somewhere the service can actually write.

    Host key *policy* is deliberately left alone: `StrictHostKeyChecking` stays
    at whatever the operator configured. TMS is repairing a condition it created
    by sandboxing the unit - it is not deciding whether host keys get verified
    on a host that holds SSH access to every Trino node (D-009).
    """
    environment = dict(os.environ)
    environment["HOME"] = state_dir
    environment["ANSIBLE_HOME"] = os.path.join(state_dir, ".ansible")
    environment["ANSIBLE_LOCAL_TEMP"] = os.path.join(state_dir, ".ansible", "tmp")
    environment["ANSIBLE_SSH_ARGS"] = " ".join(
        list(_DEFAULT_SSH_ARGS)
        + ["-o", "UserKnownHostsFile=" + known_hosts_path(state_dir)])
    return environment


class AnsibleRestartExecutor(RestartExecutor):
    """Runs one configured playbook, for one known cluster, with no shell."""

    automated = True
    name = "ansible"

    def __init__(
        self,
        playbook: str,
        cluster_inventories: Dict[str, str],
        binary: str = "ansible-playbook",
        timeout_seconds: float = 900.0,
        extra_vars: Optional[Dict[str, str]] = None,
        log_dir: str = "/var/log/trino-management-service",
        state_dir: str = "/var/lib/trino-management-service",
        runner: Optional[Any] = None,
    ) -> None:
        if not playbook or not os.path.isabs(playbook):
            raise AnsibleError(
                "cluster_ops.ansible.playbook must be an absolute path")
        if runner is None and not os.path.isfile(playbook):
            raise AnsibleError("playbook not found: {}".format(playbook))

        self.playbook = playbook
        self.cluster_inventories = dict(cluster_inventories or {})
        for cluster, path in self.cluster_inventories.items():
            if not os.path.isabs(path):
                raise AnsibleError(
                    "inventory for {!r} must be an absolute path".format(cluster))
            if runner is None and not os.path.isfile(path):
                raise AnsibleError(
                    "inventory not found for {!r}: {}".format(cluster, path))
        # ⛔ Checked here, not at run time. Discovering that ansible-playbook
        # is not on this host's PATH *during* a restart means finding out with
        # the cluster already drained and out of rotation. At construction it
        # is just a config error, and build_executor falls back to manual.
        #
        # systemd gives a minimal PATH, so an Ansible installed into a venv or
        # a user-local bin is invisible to the service even when it works fine
        # in the operator's shell. Give the absolute path in that case.
        if runner is None and shutil.which(binary) is None and not os.path.isfile(binary):
            raise AnsibleError(
                "cluster_ops.ansible.binary {!r} was not found on this host. "
                "TMS runs the playbook itself, so Ansible must be installed on "
                "the TMS server - not only on your usual control node. If it is "
                "installed but not on the service's PATH, give the absolute "
                "path.".format(binary))
        # ⛔ Ansible aborts at import time - exit 5, "Unable to create local
        # directories '~/.ansible/tmp'" - if HOME is not writable. The tms-api
        # unit runs with ProtectHome=true, so the service account's real home is
        # inaccessible and every run would fail. Checked here so it is a startup
        # error (falls back to manual) rather than a failure discovered with a
        # cluster already drained and out of rotation.
        self.state_dir = state_dir
        if runner is None and not _writable(state_dir):
            raise AnsibleError(
                "cluster_ops.ansible.state_dir {!r} is not writable by this "
                "service. Ansible refuses to run without a writable HOME, and "
                "the unit sets ProtectHome=true. Add "
                "`StateDirectory=trino-management-service` to the systemd unit "
                "- systemd then creates and owns /var/lib/<that name>, and the "
                "name must match state_dir. Note StateDirectory takes a name, "
                "not a path: it is always relative to /var/lib."
                .format(state_dir))

        if runner is None:
            # OpenSSH writes known_hosts but does not create the directory it
            # lives in when the path is given explicitly - it only does that for
            # its own `~/.ssh`. Without this the first connection fails with a
            # message about known_hosts that reads like a connectivity problem.
            # 0700 because ssh refuses a group- or world-writable directory.
            try:
                os.makedirs(ssh_directory(state_dir), mode=0o700, exist_ok=True)
            except OSError as exc:
                raise AnsibleError(
                    "cannot create {!r} for SSH's known_hosts: {}. Ansible "
                    "would fail on every host with 'Could not create directory'."
                    .format(ssh_directory(state_dir), exc))

        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self.extra_vars = dict(extra_vars or {})
        self.log_dir = log_dir
        self._runner = runner or self._run_subprocess
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    # --------------------------------------------------------------- command

    def build_command(self, cluster: str) -> List[str]:
        """Argument list for one cluster. Raises before building anything odd.

        The cluster name selects a configured inventory file and then plays no
        further part - it never reaches the command line.
        """
        inventory = self.cluster_inventories.get(cluster)
        if inventory is None:
            # Not a cluster TMS is configured for. Refuse rather than hand an
            # unknown string to a tool that holds SSH keys.
            raise AnsibleError(
                "unknown cluster {!r} - it has no configured inventory, so TMS "
                "will not target it".format(cluster))

        command = [self.binary, "--inventory", inventory, self.playbook]
        for key, value in sorted(self.extra_vars.items()):
            command += ["--extra-vars", "{}={}".format(key, value)]
        return command

    def _run_subprocess(self, command: List[str], timeout: float,
                        on_line: Callable[[str], None]) -> Dict[str, Any]:
        """Streaming, watchdog and redaction all live in ops/process.py now -
        fleet jobs need the same behaviour, and two copies of it would drift."""
        return stream_command(command, timeout, on_line,
                              env=ansible_environment(self.state_dir),
                              cwd=self.state_dir)

    # -------------------------------------------------------------- lifecycle

    def start(self, cluster: str, sequence_id: str) -> str:
        """Launch the playbook once. Safe to call again."""
        command = self.build_command(cluster)  # validates before any work
        with self._lock:
            existing = self._runs.get(sequence_id)
            if existing and existing["state"] in (RUNNING, SUCCEEDED):
                return existing["state"]
            run = {"state": RUNNING, "cluster": cluster, "lines": [],
                   "command": command, "error": None}
            self._runs[sequence_id] = run

        def collect(line: str) -> None:
            with self._lock:
                run["lines"].append(line)

        def work():
            result = self._runner(command, self.timeout_seconds, collect)
            with self._lock:
                # A runner that returns whole output instead of streaming still
                # works; its text simply arrives in one go at the end.
                trailing = result.get("output")
                if trailing:
                    run["lines"].extend(
                        line for line in redact(trailing).splitlines() if line.strip())
                if result.get("timed_out"):
                    run["state"] = FAILED
                    run["error"] = ("the playbook did not finish within {:.0f}s"
                                    .format(self.timeout_seconds))
                elif result.get("rc") == 0:
                    run["state"] = SUCCEEDED
                else:
                    run["state"] = FAILED
                    run["error"] = result.get("error") or (
                        "ansible-playbook exited {}".format(result.get("rc")))
                if run["state"] == FAILED:
                    log.error("restart playbook failed for %s: %s",
                              cluster, run["error"])

        thread = threading.Thread(target=work, daemon=True)
        run["thread"] = thread
        thread.start()
        return RUNNING

    def status(self, cluster: str, sequence_id: str) -> str:
        """RUNNING / SUCCEEDED / FAILED, or UNKNOWN after a TMS restart.

        UNKNOWN is not a failure and not a success. TMS lost sight of the
        playbook, so it says so rather than guessing - restoring traffic on a
        guess is exactly what this sequence exists to prevent.
        """
        with self._lock:
            run = self._runs.get(sequence_id)
        if run is None:
            return UNKNOWN
        return run["state"]

    def result(self, sequence_id: str) -> Dict[str, Any]:
        with self._lock:
            run = self._runs.get(sequence_id)
            if run is None:
                return {"state": UNKNOWN}
            return {"state": run["state"], "error": run["error"],
                    "output": "\n".join(run["lines"]),
                    "command": list(run["command"])}

    def lines_since(self, sequence_id: str, index: int = 0) -> List[str]:
        """Output produced after `index` lines, for incremental display.

        The caller tracks how much it has already shown, so a live view can
        poll without re-reading - or worse, re-recording - the whole log.
        """
        with self._lock:
            run = self._runs.get(sequence_id)
            if run is None:
                return []
            return list(run["lines"][max(0, int(index)):])

    def describe(self, cluster: str) -> Dict[str, Any]:
        return {
            "automated": True,
            "title": "TMS will restart {} with Ansible".format(cluster),
            "instructions": (
                "TMS runs the configured playbook and waits for it to finish. "
                "Traffic is not restored until health is GOOD afterwards."
            ),
        }
