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
import subprocess
import threading
from typing import Any, Dict, List, Optional

from tms.ops.executor import FAILED, RUNNING, SUCCEEDED, RestartExecutor

log = logging.getLogger(__name__)

UNKNOWN = "unknown"

# A conservative name shape. The value is also matched against configured
# clusters, so this is belt-and-braces against anything odd reaching argv.
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_REDACT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b(\s*[:=]\s*)(\S+)")


class AnsibleError(Exception):
    """Configuration or invocation problem, raised before anything runs."""


def redact(text: str) -> str:
    """Mask obvious `key: value` secrets in captured output.

    Best effort only - Ansible output is not structured and this cannot be
    exhaustive. The log is treated as sensitive regardless of what this catches.
    """
    return _REDACT.sub(lambda m: m.group(1) + m.group(2) + "***", text or "")


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
        log_dir: str = "/var/log/tms",
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

    def _run_subprocess(self, command: List[str], timeout: float) -> Dict[str, Any]:
        try:
            completed = subprocess.run(  # noqa: S603 - list form, no shell
                command, capture_output=True, text=True, timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return {"rc": None, "timed_out": True, "output": ""}
        except OSError as exc:
            return {"rc": None, "error": str(exc), "output": ""}
        return {
            "rc": completed.returncode,
            "output": redact((completed.stdout or "") + (completed.stderr or "")),
        }

    # -------------------------------------------------------------- lifecycle

    def start(self, cluster: str, sequence_id: str) -> str:
        """Launch the playbook once. Safe to call again."""
        command = self.build_command(cluster)  # validates before any work
        with self._lock:
            existing = self._runs.get(sequence_id)
            if existing and existing["state"] in (RUNNING, SUCCEEDED):
                return existing["state"]
            run = {"state": RUNNING, "cluster": cluster, "output": "",
                   "command": command, "error": None}
            self._runs[sequence_id] = run

        def work():
            result = self._runner(command, self.timeout_seconds)
            with self._lock:
                run["output"] = result.get("output", "")
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
                    "output": run["output"], "command": list(run["command"])}

    def describe(self, cluster: str) -> Dict[str, Any]:
        return {
            "automated": True,
            "title": "TMS will restart {} with Ansible".format(cluster),
            "instructions": (
                "TMS runs the configured playbook and waits for it to finish. "
                "Traffic is not restored until health is GOOD afterwards."
            ),
        }
