"""Running the read-only scan, and what it refuses.

⛔ The property being protected is that this cannot change a node. It runs one
playbook the operator installed, on one inventory TMS already had, and stores
what it printed. Every test here is about a boundary, not about output shaping -
that lives in test_configscan.py.
"""

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.api.errors import Forbidden, InvalidRequest, NotFound  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    KIND_CONFIG,
    InMemorySnapshotRepository,
)
from tms.core.config import build_config  # noqa: E402
from tms.ops.configscan import MARKER  # noqa: E402
from tms.ops.configservice import ConfigScanService  # noqa: E402

ADMIN = Principal("sre.kim", ["admin"])
VIEWER = Principal("reader", ["viewer"])


def a_config():
    return build_config({
        "clusters": [{"name": n, "coordinator_url": "https://{}.invalid:8443".format(n),
                      "expected_workers": 2} for n in ("prod-a", "dev-a")],
        "trino": {"user": "tms-svc", "password": "pw"},
        "database": {"url": "postgresql://u:p@h:5432/d"},
    })


def a_service(runner=None, development=(), snapshots=None):
    return ConfigScanService(
        config=a_config(), snapshots=snapshots or InMemorySnapshotRepository(),
        inventories={"prod-a": "/etc/tms/prod-a.ini", "dev-a": "/etc/tms/dev-a.ini"},
        playbook="/etc/tms/collect-config.yml",
        development_clusters=list(development),
        runner=runner or (lambda command, timeout, on_line: {"rc": 0}))


def wait(service, cluster="prod-a"):
    import time

    for _ in range(200):
        if not service.is_scanning(cluster):
            return
        time.sleep(0.01)
    raise AssertionError("the scan never finished")


class PermissionTest(unittest.TestCase):
    def test_a_viewer_may_read_but_not_scan(self):
        """⛔ A scan is a read, but a read that opens SSH to every node. That
        is not something a viewer sets off."""
        service = a_service()
        service.get(VIEWER, "prod-a")
        with self.assertRaises(Forbidden):
            service.scan(VIEWER, "prod-a")

    def test_an_unknown_cluster_is_404(self):
        with self.assertRaises(NotFound):
            a_service().scan(ADMIN, "nope")


class CommandTest(unittest.TestCase):
    def test_the_cluster_name_never_reaches_the_command_line(self):
        """⛔ D-009's rule, kept here too: the name selects an inventory file
        and nothing else. There is no host argument to get wrong."""
        seen = {}

        def runner(command, timeout, on_line):
            seen["command"] = command
            return {"rc": 0}

        service = a_service(runner=runner)
        service.scan(ADMIN, "prod-a")
        wait(service)

        self.assertEqual(
            ["ansible-playbook", "--inventory", "/etc/tms/prod-a.ini",
             "/etc/tms/collect-config.yml"], seen["command"])
        self.assertNotIn("prod-a", " ".join(seen["command"][3:]))

    def test_two_scans_of_one_cluster_do_not_overlap(self):
        import threading

        gate = threading.Event()

        def runner(command, timeout, on_line):
            gate.wait(2)
            return {"rc": 0}

        service = a_service(runner=runner)
        service.scan(ADMIN, "prod-a")
        with self.assertRaises(InvalidRequest):
            service.scan(ADMIN, "prod-a")
        gate.set()
        wait(service)


class StorageTest(unittest.TestCase):
    def test_what_the_playbook_printed_is_stored_and_compared(self):
        line = MARKER + json.dumps({
            "host": "w1", "role": "worker", "reachable": True,
            "files": {}, "valid_names": ["query.max-memory"]})

        def runner(command, timeout, on_line):
            on_line("TASK [collect] " + "*" * 20)
            on_line("ok: [w1] => " + line)
            return {"rc": 0}

        snapshots = InMemorySnapshotRepository()
        service = a_service(runner=runner, snapshots=snapshots)
        service.scan(ADMIN, "prod-a")
        wait(service)

        stored = snapshots.load("prod-a", KIND_CONFIG)
        self.assertEqual(["w1"], [n["host"] for n in stored.payload["nodes"]])
        self.assertTrue(service.get(ADMIN, "prod-a")["scanned"])

    def test_a_failed_playbook_keeps_what_it_did_collect(self):
        """⛔ Throwing away a partial scan would leave the screen showing the
        previous one as though it were current."""
        line = MARKER + json.dumps({"host": "w1", "role": "worker",
                                    "reachable": True, "files": {},
                                    "valid_names": []})

        def runner(command, timeout, on_line):
            on_line(line)
            return {"rc": 2, "error": "one host unreachable"}

        service = a_service(runner=runner)
        service.scan(ADMIN, "prod-a")
        wait(service)

        result = service.get(ADMIN, "prod-a")
        self.assertEqual(["w1"], [n["host"] for n in result["nodes"]])
        self.assertIn("unreachable", result["error"])

    def test_a_silent_playbook_says_which_playbook_to_check(self):
        service = a_service(runner=lambda c, t, on_line: {"rc": 0})
        service.scan(ADMIN, "prod-a")
        wait(service)
        self.assertIn("collect-config.yml", service.get(ADMIN, "prod-a")["error"])

    def test_nothing_scanned_yet_is_not_an_error(self):
        result = a_service().get(ADMIN, "prod-a")
        self.assertFalse(result["scanned"])
        self.assertTrue(result["agree"], "silence is not agreement, but it is "
                                         "not disagreement either")
        self.assertEqual([], result["nodes"])


class DevelopmentClusterTest(unittest.TestCase):
    """D-018: the development cluster's worker count changes with whatever is
    being tested, so a node that did not answer is not drift there."""

    def test_a_development_cluster_is_marked_and_forgives_a_missing_node(self):
        line = MARKER + json.dumps({"host": "w2", "role": "worker",
                                    "reachable": False, "error": "host is down",
                                    "files": {}, "valid_names": []})

        def runner(command, timeout, on_line):
            on_line(line)
            return {"rc": 0}

        service = a_service(runner=runner, development=("dev-a",))
        service.scan(ADMIN, "dev-a")
        wait(service, "dev-a")

        result = service.get(ADMIN, "dev-a")
        self.assertTrue(result["development"])
        self.assertEqual([], [f for f in result["findings"]
                              if f["kind"] == "unreachable"])

    def test_production_reports_the_same_node_as_a_finding(self):
        line = MARKER + json.dumps({"host": "w2", "role": "worker",
                                    "reachable": False, "error": "host is down",
                                    "files": {}, "valid_names": []})

        def runner(command, timeout, on_line):
            on_line(line)
            return {"rc": 0}

        service = a_service(runner=runner, development=("dev-a",))
        service.scan(ADMIN, "prod-a")
        wait(service)

        result = service.get(ADMIN, "prod-a")
        self.assertFalse(result["development"])
        self.assertTrue([f for f in result["findings"]
                         if f["kind"] == "unreachable"])


if __name__ == "__main__":
    unittest.main()
