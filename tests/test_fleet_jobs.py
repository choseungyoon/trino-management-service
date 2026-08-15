"""Running a configured playbook against a cluster (FR-FL-04/05).

Two things are protected here, and only one of them is the feature.

The feature is: an operator can press a scripted procedure and watch it, with a
reason and a record.

The other is that this must never become a way around the safe restart
sequence. TMS sees a configured path and an exit code - it has no idea whether a
playbook drains anything - so the separation is kept by refusing to run the
restart playbook as a job, and by never letting a request choose what runs.
"""

import os
import sys
import time
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.api.errors import Forbidden, InvalidRequest  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.collector.snapshot import InMemorySnapshotRepository  # noqa: E402
from tms.core.audit import (  # noqa: E402
    ACTION_FLEET_JOB,
    SUCCESS,
    AuditGuard,
    InMemoryAuditRepository,
)
from tms.core.config import ConfigError, build_config  # noqa: E402
from tms.core.configcheck import FAIL, OK, Report, check_fleet_jobs  # noqa: E402
from tms.fleet.jobs import (  # noqa: E402
    FAILED,
    SUCCEEDED,
    UNKNOWN,
    JobError,
    JobRunner,
    build_jobs,
    outcome,
)
from tms.fleet.jobstore import InMemoryJobRepository  # noqa: E402
from tms.fleet.service import FleetService  # noqa: E402

ADMIN = Principal("op", ["admin"], ip="10.0.0.9")
VIEWER = Principal("watcher", ["viewer"], ip="10.0.0.9")

JOBS = {
    "scale_out": {
        "playbook": __file__,
        "title": "Add workers",
        "parameters": {"count": {"min": 1, "max": 4, "default": 2}},
    },
}


class ParameterTest(unittest.TestCase):
    def test_a_declared_parameter_is_coerced_and_bounded(self):
        job = build_jobs(JOBS)["scale_out"]
        self.assertEqual({"count": 3}, job.clean({"count": "3"}))
        for bad in ("0", "5", "", "two", None):
            with self.assertRaises(JobError, msg=bad):
                job.clean({"count": bad})

    def test_a_missing_parameter_falls_back_to_its_default(self):
        job = build_jobs(JOBS)["scale_out"]
        self.assertEqual({"count": 2}, job.clean({}))

    def test_undeclared_input_is_dropped_rather_than_passed_on(self):
        """"Declared in config" has to be the only way anything reaches the
        playbook, or the request is choosing what runs."""
        job = build_jobs(JOBS)["scale_out"]
        self.assertEqual({"count": 2}, job.clean({"count": 2, "hosts": "; rm -rf /"}))

    def test_a_relative_playbook_is_refused(self):
        with self.assertRaises(JobError):
            build_jobs({"x": {"playbook": "playbooks/scale.yml"}})

    def test_an_impossible_range_is_refused_at_load(self):
        with self.assertRaises(JobError):
            build_jobs({"x": {"playbook": __file__,
                              "parameters": {"n": {"min": 5, "max": 2}}}})

    def test_a_malformed_job_fails_config_loading(self):
        """A startup error, not something found while adding capacity."""
        with self.assertRaises(ConfigError):
            build_config({
                "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid",
                              "expected_workers": 11}],
                "trino": {"user": "u", "password": "p"},
                "database": {"url": "postgresql://u:p@h/d"},
                "fleet": {"enabled": True, "inventories": {"prod-a": __file__},
                          "node_url_template": "https://{address}:8443",
                          "jobs": {"bad": {"playbook": "relative.yml"}}},
            })


class CommandTest(unittest.TestCase):
    def runner(self):
        return JobRunner(jobs=build_jobs(JOBS),
                         cluster_inventories={"prod-a": "/etc/tms/ansible/cluster1.ini"})

    def test_the_cluster_chooses_an_inventory_and_never_reaches_argv(self):
        """D-009's rule, unchanged: no host name on the command line at all."""
        command = self.runner().build_command("scale_out", "prod-a", {"count": 2})
        self.assertIn("/etc/tms/ansible/cluster1.ini", command)
        self.assertNotIn("prod-a", command)

    def test_parameters_arrive_as_integers(self):
        command = self.runner().build_command("scale_out", "prod-a", {"count": 3})
        self.assertIn("count=3", command)

    def test_an_unknown_job_is_refused_before_anything_runs(self):
        with self.assertRaises(JobError):
            self.runner().build_command("whatever", "prod-a", {})

    def test_a_cluster_with_no_inventory_is_refused(self):
        with self.assertRaises(JobError):
            self.runner().build_command("scale_out", "prod-b", {"count": 1})


class OutcomeTest(unittest.TestCase):
    def test_a_clean_exit_succeeds(self):
        self.assertEqual(SUCCEEDED, outcome({"rc": 0})["state"])

    def test_a_non_zero_exit_fails_and_says_which(self):
        result = outcome({"rc": 2})
        self.assertEqual(FAILED, result["state"])
        self.assertIn("2", result["error"])

    def test_a_timeout_says_the_work_was_not_undone(self):
        """A killed playbook leaves whatever it already did in place, and the
        person reading this is deciding whether to run it again."""
        result = outcome({"rc": None, "timed_out": True})
        self.assertEqual(FAILED, result["state"])
        self.assertIn("has been done", result["error"])


def build(jobs=JOBS, runner_result=None):
    config = build_config({
        "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid",
                      "expected_workers": 11}],
        "trino": {"user": "u", "password": "p"},
        "database": {"url": "postgresql://u:p@h/d"},
        "fleet": {"enabled": True, "inventories": {"prod-a": __file__},
                  "node_url_template": "https://{address}:8443", "jobs": jobs},
    })
    audit = InMemoryAuditRepository()

    def fake_runner(command, timeout, on_line, env=None, cwd=None):
        on_line("PLAY [add workers]")
        return runner_result or {"rc": 0}

    repository = InMemoryJobRepository()
    service = FleetService(
        config=config, snapshots=InMemorySnapshotRepository(),
        audit_guard=AuditGuard(audit), transport_factory=lambda: None,
        job_runner=JobRunner(jobs=build_jobs(jobs) if jobs else {},
                             cluster_inventories={"prod-a": __file__},
                             runner=fake_runner) if jobs else None,
        job_repository=repository if jobs else None)
    return service, repository, audit


class ServiceTest(unittest.TestCase):
    def _wait(self, repository, run_id):
        for _ in range(300):
            run = repository.get(run_id)
            if run and run["state"] != "RUNNING":
                return run
            time.sleep(0.01)
        return repository.get(run_id)

    def test_a_viewer_cannot_run_a_job(self):
        service, repository, _a = build()
        with self.assertRaises(Forbidden):
            service.start_job(VIEWER, "prod-a", "scale_out", {"count": 1}, "no")
        self.assertEqual([], repository.runs)

    def test_a_reason_is_required(self):
        service, repository, _a = build()
        with self.assertRaises(InvalidRequest):
            service.start_job(ADMIN, "prod-a", "scale_out", {"count": 1}, "  ")
        self.assertEqual([], repository.runs)

    def test_an_out_of_range_parameter_is_refused_before_the_record(self):
        service, repository, _a = build()
        with self.assertRaises(InvalidRequest):
            service.start_job(ADMIN, "prod-a", "scale_out", {"count": 99}, "why")
        self.assertEqual([], repository.runs, "nothing was recorded or started")

    def test_a_run_is_audited_and_recorded_with_its_reason(self):
        service, repository, audit = build()
        run = service.start_job(ADMIN, "prod-a", "scale_out", {"count": 2},
                                "peak is coming on Monday")
        finished = self._wait(repository, run["id"])
        self.assertEqual(SUCCEEDED, finished["state"])
        self.assertEqual({"count": 2}, finished["parameters"])

        record = audit.records[-1]
        self.assertEqual(ACTION_FLEET_JOB, record.action_type)
        self.assertEqual("peak is coming on Monday", record.reason)
        self.assertEqual(SUCCESS, record.outcome)

    def test_the_playbook_output_is_kept(self):
        service, repository, _a = build()
        run = service.start_job(ADMIN, "prod-a", "scale_out", {"count": 1}, "why")
        self._wait(repository, run["id"])
        messages = [line["message"] for line in repository.get(run["id"])["output"]]
        self.assertIn("PLAY [add workers]", messages)

    def test_a_failing_playbook_is_recorded_as_failed(self):
        service, repository, _a = build(runner_result={"rc": 4})
        run = service.start_job(ADMIN, "prod-a", "scale_out", {"count": 1}, "why")
        finished = self._wait(repository, run["id"])
        self.assertEqual(FAILED, finished["state"])
        self.assertEqual(4, finished["exit_code"])

    def test_a_second_job_on_the_same_cluster_is_refused(self):
        """Two playbooks writing one inventory is not a race to resolve later."""
        service, repository, _a = build()
        repository.create("prod-a", "scale_out", "someone", ["admin"], "first", {})
        with self.assertRaises(InvalidRequest) as caught:
            service.start_job(ADMIN, "prod-a", "scale_out", {"count": 1}, "second")
        self.assertIn("already running", str(caught.exception))

    def test_jobs_are_invisible_when_none_are_configured(self):
        service, _r, _a = build(jobs={})
        self.assertFalse(service.jobs_enabled)
        self.assertFalse(service.list_jobs(VIEWER, "prod-a")["enabled"])

    def test_an_orphaned_run_becomes_unknown_not_failed(self):
        """The playbook may have finished perfectly. What is true is that nobody
        watched it end - different things to tell someone deciding whether to
        run it again."""
        repository = InMemoryJobRepository()
        run = repository.create("prod-a", "scale_out", "op", ["admin"], "why", {})
        repository.finish(run["id"], UNKNOWN)
        self.assertEqual(UNKNOWN, repository.get(run["id"])["state"])


class ConfigCheckTest(unittest.TestCase):
    def _report(self, jobs, restart_playbook=""):
        config = build_config({
            "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid",
                          "expected_workers": 11}],
            "trino": {"user": "u", "password": "p"},
            "database": {"url": "postgresql://u:p@h/d"},
            "fleet": {"enabled": True, "inventories": {"prod-a": __file__},
                      "node_url_template": "https://{address}:8443", "jobs": jobs},
            "cluster_ops": {"restart_mode": "manual",
                            "ansible": {"playbook": restart_playbook,
                                        "inventories": {"prod-a": __file__}}},
        })
        report = Report()
        check_fleet_jobs(report, config)
        return report

    def test_a_job_pointing_at_the_restart_playbook_is_refused(self):
        """The path CLAUDE.md rule 5 exists to close: a button that restarts a
        cluster without stopping traffic first."""
        report = self._report(JOBS, restart_playbook=__file__)
        failures = [r for r in report.rows if r[0] == FAIL]
        self.assertEqual(1, len(failures), report.rows)
        self.assertIn("절대규칙 5", failures[0][2])

    def test_a_missing_playbook_is_refused(self):
        report = self._report({"x": {"playbook": "/nonexistent/scale.yml"}})
        self.assertTrue(any(r[0] == FAIL for r in report.rows))

    def test_no_jobs_is_reported_as_the_normal_state(self):
        report = self._report({})
        self.assertEqual([OK], [r[0] for r in report.rows])


if __name__ == "__main__":
    unittest.main()
