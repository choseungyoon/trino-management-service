"""Running a configured playbook against a cluster (FR-FL-04/05).

Scale-out is what this was built for: adding workers is a scripted procedure the
platform team already has, and the only thing missing was somewhere to press it
from and a record of what happened.

⛔ **These runs are not the safe restart sequence, and must never become a way
around it.** Nothing here checks that a cluster was drained, because nothing
here knows what the playbook does - TMS sees a path and an exit code. That is
why `fleet.jobs` must not point at a playbook that restarts anything;
`tms-config-check` refuses that specific case, and CLAUDE.md rule 5 is why.
Restarts go through FR-CO-02, which has the gates.

The constraints from D-009 carry over unchanged, because the blast radius is the
same - the TMS host holds SSH to every node:

* **The playbook is configuration, never input.** A request names a *job key*,
  which selects among what an administrator already declared. A path never
  arrives in a request.
* **No shell.** Argument lists only.
* **The cluster selects an inventory file**, and the name itself never reaches
  the command line.
* **Parameters are integers only**, declared in config with bounds, and
  range-checked here. A scale-out that cannot say "how many" is not much use,
  but a string parameter would hand the request a way onto the command line, so
  there are none. An integer cannot be anything but an integer.

Python 3.9 compatible.
"""

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from tms.ops.process import stream_command

log = logging.getLogger(__name__)

RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
#: tms-api died while the job was running. Not a synonym for failure: the
#: playbook may well have finished, and nobody can now say. Reporting either
#: outcome would be a guess about production.
UNKNOWN = "UNKNOWN"

TERMINAL = (SUCCEEDED, FAILED, UNKNOWN)


class JobError(Exception):
    """Configuration or invocation problem, raised before anything runs."""


class JobParameter:
    """One declared integer input, with the range it may take.

    Bounds are required rather than optional. "How many workers" with no ceiling
    is a request to provision until something else breaks, and the person typing
    it is usually the person least placed to know where that is.
    """

    __slots__ = ("name", "label", "minimum", "maximum", "default")

    def __init__(self, name: str, label: str = "", minimum: int = 1,
                 maximum: int = 10, default: Optional[int] = None) -> None:
        self.name = name
        self.label = label or name
        self.minimum = int(minimum)
        self.maximum = int(maximum)
        self.default = int(default) if default is not None else self.minimum
        if self.minimum > self.maximum:
            raise JobError(
                "parameter {!r}: min {} is above max {}".format(
                    name, self.minimum, self.maximum))

    def clean(self, raw) -> int:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            raise JobError("{} must be a whole number.".format(self.label))
        if not (self.minimum <= value <= self.maximum):
            raise JobError("{} must be between {} and {}.".format(
                self.label, self.minimum, self.maximum))
        return value


class JobDefinition:
    """One thing an administrator has declared TMS may run."""

    __slots__ = ("key", "title", "description", "playbook", "parameters",
                 "timeout_seconds", "confirm")

    def __init__(self, key: str, playbook: str, title: str = "",
                 description: str = "", parameters=None,
                 timeout_seconds: float = 1800.0) -> None:
        if not playbook or not os.path.isabs(playbook):
            raise JobError(
                "fleet.jobs.{}.playbook must be an absolute path".format(key))
        self.key = key
        self.playbook = playbook
        self.title = title or key.replace("_", " ").capitalize()
        self.description = description
        self.parameters: List[JobParameter] = list(parameters or [])
        self.timeout_seconds = float(timeout_seconds)

    def clean(self, submitted: Dict[str, Any]) -> Dict[str, int]:
        """Validate every declared parameter. Undeclared input is dropped.

        Dropped rather than rejected: an extra form field is not an attack worth
        a 400, and silently ignoring it is what keeps "declared in config" the
        only way anything reaches the playbook.
        """
        return {p.name: p.clean(submitted.get(p.name, p.default))
                for p in self.parameters}


def build_jobs(raw: Dict[str, Any]) -> Dict[str, JobDefinition]:
    """`fleet.jobs` from config.yaml -> definitions, or raise."""
    jobs = {}
    for key, entry in sorted((raw or {}).items()):
        if not isinstance(entry, dict):
            raise JobError("fleet.jobs.{} must be a mapping".format(key))
        parameters = []
        for name, spec in sorted((entry.get("parameters") or {}).items()):
            spec = spec or {}
            parameters.append(JobParameter(
                name=name, label=spec.get("label", ""),
                minimum=spec.get("min", 1), maximum=spec.get("max", 10),
                default=spec.get("default")))
        jobs[key] = JobDefinition(
            key=key, playbook=str(entry.get("playbook") or ""),
            title=str(entry.get("title") or ""),
            description=str(entry.get("description") or ""),
            parameters=parameters,
            timeout_seconds=float(entry.get("timeout_seconds", 1800)))
    return jobs


class JobRunner:
    """Starts jobs and reports on the ones this process started.

    Deliberately holds no database. Persistence is the repository's job; this
    owns the subprocess and the lines coming out of it, and hands each line to a
    callback as it arrives so the caller can store and show it.
    """

    def __init__(self, jobs: Dict[str, JobDefinition],
                 cluster_inventories: Dict[str, str],
                 binary: str = "ansible-playbook",
                 state_dir: str = "/var/lib/trino-management-service",
                 runner=None) -> None:
        self.jobs = dict(jobs or {})
        self.cluster_inventories = dict(cluster_inventories or {})
        self.binary = binary
        self.state_dir = state_dir
        self._runner = runner or stream_command
        self._threads: Dict[Any, threading.Thread] = {}
        self._lock = threading.Lock()

    def definition(self, key: str) -> JobDefinition:
        job = self.jobs.get(key)
        if job is None:
            # Not a job TMS is configured for. Refuse rather than hand an
            # unknown string to a tool that holds SSH keys.
            raise JobError("unknown job {!r}".format(key))
        return job

    def build_command(self, key: str, cluster: str,
                      parameters: Dict[str, int]) -> List[str]:
        """Argument list for one run. Raises before building anything odd."""
        job = self.definition(key)
        inventory = self.cluster_inventories.get(cluster)
        if inventory is None:
            raise JobError(
                "cluster {!r} has no configured inventory, so TMS will not "
                "target it".format(cluster))

        command = [self.binary, "--inventory", inventory, job.playbook]
        for name in sorted(parameters):
            # int() again at the boundary. `clean` already did it, but this is
            # the line that becomes a command, and it should be readable as
            # safe without tracing where its input came from.
            command += ["--extra-vars", "{}={}".format(name, int(parameters[name]))]
        return command

    def start(self, key: str, cluster: str, parameters: Dict[str, int],
              on_line, on_finish) -> List[str]:
        """Launch in the background. Returns the command, for the record.

        `on_line` is called from the worker thread for every line; `on_finish`
        once, with the outcome. Both are the caller's chance to persist - this
        keeps nothing.
        """
        job = self.definition(key)
        command = self.build_command(key, cluster, parameters)

        def work() -> None:
            from tms.ops.ansible import ansible_environment

            try:
                result = self._runner(command, job.timeout_seconds, on_line,
                                      env=ansible_environment(self.state_dir),
                                      cwd=self.state_dir)
            except Exception as exc:  # noqa: BLE001 - a thread must not vanish
                log.exception("fleet job %s on %s crashed", key, cluster)
                on_finish({"state": FAILED, "error": str(exc)})
                return
            on_finish(outcome(result))

        thread = threading.Thread(target=work, name="fleet-job", daemon=True)
        with self._lock:
            self._threads[(key, cluster)] = thread
        thread.start()
        return command


def outcome(result: Dict[str, Any]) -> Dict[str, Any]:
    """What the runner's result means for the run row."""
    if result.get("timed_out"):
        return {"state": FAILED, "exit_code": None,
                "error": "The playbook was still running when the timeout "
                         "expired and was killed. Whatever it had already done "
                         "on the nodes has been done."}
    if result.get("error"):
        return {"state": FAILED, "exit_code": None, "error": result["error"]}
    rc = result.get("rc")
    if rc == 0:
        return {"state": SUCCEEDED, "exit_code": 0, "error": None}
    return {"state": FAILED, "exit_code": rc,
            "error": "The playbook exited with code {}.".format(rc)}
