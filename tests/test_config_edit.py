"""Editing config.properties: the typo gate, the development gate, and what a
deploy deliberately does not do.

⛔ The property being protected hardest is what is *absent*: nothing here
restarts anything. Trino reads config.properties at startup, so a deploy leaves
a changed file on a cluster still running the old values - and restarting is
the safe sequence's job, which drains first.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.api.errors import Forbidden, InvalidRequest  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    KIND_CONFIG,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.core.audit import (  # noqa: E402
    ACTION_CONFIG_DEPLOY,
    AuditGuard,
    InMemoryAuditRepository,
)
from tms.core.config import build_config  # noqa: E402
from tms.ops import configedit  # noqa: E402
from tms.ops.configeditservice import ConfigEditService  # noqa: E402
from tms.ops.configeditstore import InMemoryConfigChangeRepository  # noqa: E402

ADMIN = Principal("sre.kim", ["admin"])
VIEWER = Principal("reader", ["viewer"])

NAMES = ["query.max-memory", "query.max-memory-per-node", "http-server.http.port",
         "task.concurrency", "node-scheduler.include-coordinator"]


def a_scan(names=None, nodes=None):
    """A config snapshot in the shape the collector's playbook produces."""
    return {"nodes": nodes if nodes is not None else [
        {"host": "c1", "role": "coordinator", "reachable": True, "error": None,
         "files": {}, "properties": {"query.max-memory": "900GB"},
         "valid_names": list(NAMES if names is None else names)},
        {"host": "w1", "role": "worker", "reachable": True, "error": None,
         "files": {}, "properties": {"task.concurrency": "16"},
         "valid_names": list(NAMES if names is None else names)},
        {"host": "w2", "role": "worker", "reachable": True, "error": None,
         "files": {}, "properties": {"task.concurrency": "16"},
         "valid_names": list(NAMES if names is None else names)},
    ]}


def build(runner=None, development=("dev-a",), scanned=("dev-a", "prod-a"),
          names=None):
    config = build_config({
        "clusters": [{"name": n, "coordinator_url": "https://{}.invalid:8443".format(n),
                      "expected_workers": 2} for n in ("dev-a", "prod-a")],
        "trino": {"user": "tms-svc", "password": "pw"},
        "database": {"url": "postgresql://u:p@h:5432/d"},
    })
    snapshots = InMemorySnapshotRepository()
    for cluster in scanned:
        snapshots.save(Snapshot(cluster, KIND_CONFIG, utcnow(),
                                payload=a_scan(names)))
    audit = InMemoryAuditRepository()
    service = ConfigEditService(
        config=config, repository=InMemoryConfigChangeRepository(),
        snapshots=snapshots, audit_guard=AuditGuard(audit),
        playbook="/etc/tms/ansible/deploy-config.yml",
        inventories={"dev-a": "/etc/tms/dev-a.ini", "prod-a": "/etc/tms/prod-a.ini"},
        development_clusters=list(development),
        runner=runner or (lambda command, timeout, on_line: {"rc": 0}))
    return service, audit


def wait(service, cluster):
    import time

    for _ in range(300):
        if not service.is_busy(cluster):
            return
        time.sleep(0.01)
    raise AssertionError("the deployment never finished")


def a_change(service, entries=None, role="all"):
    return service.create(
        ADMIN, title="Raise the memory ceiling", target_role=role,
        entries=entries or [{"key": "query.max-memory", "action": "set",
                             "value": "900GB"}],
        notes=None, reason="month-end reporting needs it")


class NoRestartTest(unittest.TestCase):
    """⛔ The absence being protected."""

    def test_the_command_edits_a_file_and_says_nothing_about_restarting(self):
        seen = {}

        def runner(command, timeout, on_line):
            seen["command"] = command
            return {"rc": 0}

        service, _ = build(runner=runner)
        change = a_change(service)
        service.deploy(ADMIN, change["id"], "dev-a", reason="trying it here first")
        wait(service, "dev-a")

        joined = " ".join(seen["command"])
        for word in ("restart", "systemctl", "service"):
            self.assertNotIn(word, joined.lower())
        self.assertIn("deploy-config.yml", joined)

    def test_no_host_name_reaches_the_command_line(self):
        seen = {}

        def runner(command, timeout, on_line):
            seen["command"] = command
            return {"rc": 0}

        service, _ = build(runner=runner)
        change = a_change(service, role="worker")
        service.deploy(ADMIN, change["id"], "dev-a", reason="here first")
        wait(service, "dev-a")

        # The target is one of three words; the hosts come from the inventory
        # file, which is selected by path.
        self.assertIn("target_role=worker", seen["command"])
        for host in ("c1", "w1", "w2"):
            self.assertNotIn(host, " ".join(seen["command"]).split("/")[-1])


class ValidateTest(unittest.TestCase):
    def test_a_credential_shaped_key_must_carry_an_env_reference(self):
        with self.assertRaises(configedit.ConfigEditError) as caught:
            configedit.validate("x", "all", [
                {"key": "http-server.https.keystore.key", "action": "set",
                 "value": "hunter2"}])
        self.assertIn("${ENV:", str(caught.exception))
        configedit.validate("x", "all", [
            {"key": "http-server.https.keystore.key", "action": "set",
             "value": "${ENV:KEYSTORE_PASSWORD}"}])

    def test_an_empty_value_is_refused_rather_than_treated_as_a_removal(self):
        # "" is a value in a properties file, so guessing would write it.
        with self.assertRaises(configedit.ConfigEditError):
            configedit.validate("x", "all", [
                {"key": "task.concurrency", "action": "set", "value": ""}])

    def test_a_newline_would_become_a_second_property(self):
        with self.assertRaises(configedit.ConfigEditError):
            configedit.validate("x", "all", [
                {"key": "task.concurrency", "action": "set",
                 "value": "16\nnode-scheduler.include-coordinator=true"}])

    def test_the_target_is_one_of_three_words(self):
        with self.assertRaises(configedit.ConfigEditError):
            configedit.validate("x", "trino-w1", [
                {"key": "task.concurrency", "action": "set", "value": "16"}])

    def test_the_same_key_twice_is_refused(self):
        with self.assertRaises(configedit.ConfigEditError):
            configedit.validate("x", "all", [
                {"key": "task.concurrency", "action": "set", "value": "16"},
                {"key": "task.concurrency", "action": "set", "value": "32"}])


class TypoGateTest(unittest.TestCase):
    """⛔ An unknown property name stops Trino from starting (T1-8-1)."""

    def test_an_unknown_name_is_refused_on_the_development_cluster_too(self):
        service, _ = build()
        change = a_change(service, entries=[
            {"key": "query.max-memroy", "action": "set", "value": "900GB"}])
        with self.assertRaises(InvalidRequest) as caught:
            service.deploy(ADMIN, change["id"], "dev-a", reason="typo test")
        self.assertIn("query.max-memroy", str(caught.exception))

    def test_removing_an_unknown_name_is_allowed(self):
        # The line is either absent, or present and already stopping the
        # server - taking it out is the fix, not the hazard.
        service, _ = build()
        change = a_change(service, entries=[
            {"key": "some.removed.property", "action": "unset"}])
        service.deploy(ADMIN, change["id"], "dev-a", reason="cleaning up")
        wait(service, "dev-a")

    def test_a_cluster_with_no_scan_refuses_rather_than_skipping_the_check(self):
        service, _ = build(scanned=("dev-a",))
        change = a_change(service)
        service.deploy(ADMIN, change["id"], "dev-a", reason="prove it")
        wait(service, "dev-a")
        with self.assertRaises(InvalidRequest) as caught:
            service.deploy(ADMIN, change["id"], "prod-a", reason="ship it")
        self.assertIn("has not read this cluster", str(caught.exception))

    def test_a_scan_that_produced_no_names_refuses(self):
        # V-12's "Known properties" column being empty means a wrong log path.
        # Deploying anyway would be deploying with the typo check switched off.
        service, _ = build(names=[])
        change = a_change(service)
        with self.assertRaises(InvalidRequest) as caught:
            service.deploy(ADMIN, change["id"], "dev-a", reason="anyway")
        self.assertIn("no property names", str(caught.exception))

    def test_the_name_list_is_the_intersection_across_nodes(self):
        nodes = a_scan()["nodes"]
        nodes[1]["valid_names"] = ["query.max-memory"]
        service, _ = build()
        service.snapshots.save(Snapshot(
            "dev-a", KIND_CONFIG, service.snapshots.load("dev-a", KIND_CONFIG)
            .collected_at, payload={"nodes": nodes}))
        change = a_change(service, entries=[
            {"key": "task.concurrency", "action": "set", "value": "16"}])
        with self.assertRaises(InvalidRequest) as caught:
            service.deploy(ADMIN, change["id"], "dev-a", reason="one node knows it")
        self.assertIn("task.concurrency", str(caught.exception))


class DevelopmentGateTest(unittest.TestCase):
    def test_production_is_refused_until_development_has_seen_it(self):
        service, _ = build()
        change = a_change(service)
        with self.assertRaises(InvalidRequest) as caught:
            service.deploy(ADMIN, change["id"], "prod-a", reason="ship it")
        self.assertIn("not been proved", str(caught.exception))

        service.deploy(ADMIN, change["id"], "dev-a", reason="prove it")
        wait(service, "dev-a")
        service.deploy(ADMIN, change["id"], "prod-a", reason="ship it")
        wait(service, "prod-a")

    def test_editing_a_proved_change_clears_the_proof(self):
        # ⛔ Otherwise: prove one thing on development, change a value, and
        # ship something the test never saw.
        service, _ = build()
        change = a_change(service)
        service.deploy(ADMIN, change["id"], "dev-a", reason="prove it")
        wait(service, "dev-a")
        self.assertEqual(service.repository.get(change["id"])["verified_on"], "dev-a")

        service.update(ADMIN, change["id"], title="Raise it further",
                       target_role="all",
                       entries=[{"key": "query.max-memory", "action": "set",
                                 "value": "1200GB"}],
                       notes=None, reason="more headroom")
        self.assertIsNone(service.repository.get(change["id"])["verified_on"])
        with self.assertRaises(InvalidRequest):
            service.deploy(ADMIN, change["id"], "prod-a", reason="ship it")

    def test_a_failed_development_deploy_does_not_prove_anything(self):
        service, _ = build(runner=lambda c, t, on_line: {"rc": 2})
        change = a_change(service)
        service.deploy(ADMIN, change["id"], "dev-a", reason="prove it")
        wait(service, "dev-a")
        self.assertIsNone(service.repository.get(change["id"])["verified_on"])


class PermissionTest(unittest.TestCase):
    def test_a_viewer_can_look_but_not_change_or_deploy(self):
        service, _ = build()
        change = a_change(service)
        service.overview(VIEWER, "dev-a")
        with self.assertRaises(Forbidden):
            service.create(
                VIEWER, title="x", target_role="all",
                entries=[{"key": "task.concurrency", "action": "set", "value": "1"}],
                notes=None, reason="because")
        with self.assertRaises(Forbidden):
            service.deploy(VIEWER, change["id"], "dev-a", reason="because")

    def test_every_write_needs_a_reason_and_is_audited(self):
        service, audit = build()
        with self.assertRaises(Exception):
            service.create(ADMIN, title="x", target_role="all",
                           entries=[{"key": "task.concurrency", "action": "set",
                                     "value": "1"}], notes=None, reason="")
        change = a_change(service)
        service.deploy(ADMIN, change["id"], "dev-a", reason="prove it")
        wait(service, "dev-a")
        self.assertEqual(audit.records[-1].action_type, ACTION_CONFIG_DEPLOY)
        self.assertEqual(audit.records[-1].reason, "prove it")


class AdviceTest(unittest.TestCase):
    def test_a_coordinator_only_setting_going_to_workers_is_flagged_not_blocked(self):
        # A coordinator-only value on a worker starts fine (T1-8-2), so this
        # is a sentence on the screen and never a refusal.
        service, _ = build()
        change = a_change(service)
        described = service.overview(ADMIN, "dev-a")["changes"][0]
        self.assertTrue(any("query.max-memory" in line for line in described["advice"]))
        self.assertIsNone(
            next(t for t in described["targets"] if t["cluster"] == "dev-a")["refusal"])


if __name__ == "__main__":
    unittest.main()
