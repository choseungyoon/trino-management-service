"""The node list: validation, what a scan changes, and the rendered inventory."""

import os
import sys
import tempfile
import unittest
from datetime import timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from tms.api.errors import Forbidden, InvalidRequest, NotFound  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.core.audit import (  # noqa: E402
    ACTION_CLUSTER_NODE_CHANGE,
    AuditGuard,
    InMemoryAuditRepository,
)
from tms.core.config import build_config  # noqa: E402
from tms.fleet import nodes
from tms.fleet.inventory import parse_inventory
from tms.fleet.nodeservice import NodeListService
from tms.fleet.nodestore import (
    SOURCE_DISCOVERED,
    SOURCE_MANUAL,
    InMemoryNodeRepository,
    utcnow,
)

ADMIN = Principal("sre.kim", ["admin"])
VIEWER = Principal("reader", ["viewer"])


def principal(admin=False):
    return ADMIN if admin else VIEWER


def a_config():
    return build_config({
        "clusters": [{"name": "prod-a",
                      "coordinator_url": "https://prod-a.invalid:8443",
                      "expected_workers": 2}],
        "trino": {"user": "tms-svc", "password": "pw"},
        "database": {"url": "postgresql://u:p@h:5432/d"},
    })


class ValidateTest(unittest.TestCase):
    def test_defaults_the_address_to_the_host(self):
        entry = nodes.validate("prod-a", "w1", "", "worker", ["prod-a"])
        self.assertEqual(entry["address"], "w1")

    def test_refuses_a_host_that_would_become_an_ansible_variable(self):
        # `w1 ansible_connection=local` in one field would land in the rendered
        # file as two tokens, and the second one changes how Ansible connects.
        for bad in ("w1 ansible_connection=local", "w1\nw2", "w1;rm -rf /", ""):
            with self.assertRaises(nodes.NodeError):
                nodes.validate("prod-a", bad, "", "worker", ["prod-a"])

    def test_refuses_an_unknown_role_and_an_unknown_cluster(self):
        with self.assertRaises(nodes.NodeError):
            nodes.validate("prod-a", "w1", "", "gateway", ["prod-a"])
        with self.assertRaises(nodes.NodeError):
            nodes.validate("nowhere", "w1", "", "worker", ["prod-a"])


class RenderTest(unittest.TestCase):
    def test_round_trips_through_the_inventory_parser(self):
        rows = [{"host": "c1", "address": "10.0.0.1", "role": "coordinator"},
                {"host": "w1", "address": "w1", "role": "worker"}]
        parsed = parse_inventory(nodes.render_inventory("prod-a", rows), "prod-a")
        self.assertEqual([(n.host, n.address, n.role) for n in parsed],
                         [("c1", "10.0.0.1", "coordinator"),
                          ("w1", "w1", "worker")])

    def test_emits_both_groups_even_when_one_is_empty(self):
        text = nodes.render_inventory("dev", [{"host": "c1", "address": "c1",
                                               "role": "coordinator"}])
        self.assertIn("[worker]", text)


class PlanRefreshTest(unittest.TestCase):
    def found(self, *hosts):
        return [{"cluster": "prod-a", "host": h, "address": h, "role": "worker",
                 "node_id": h, "version": "477", "state": "active"} for h in hosts]

    def test_a_node_that_stopped_answering_is_reported_not_removed(self):
        # ⛔ The whole point. A down worker still has to receive configuration.
        existing = [{"host": "w1", "source": SOURCE_DISCOVERED, "address": "w1"},
                    {"host": "w2", "source": SOURCE_DISCOVERED, "address": "w2"}]
        plan = nodes.plan_refresh(existing, self.found("w1"))
        self.assertEqual([r["host"] for r in plan["silent"]], ["w2"])
        self.assertNotIn("removed", plan)

    def test_an_imported_alias_is_matched_by_its_address_not_added_twice(self):
        # An imported row is named by whatever the inventory called it;
        # discovery only ever learns the host part of http_uri. Matching on the
        # name alone would list every node twice and double the deploy targets.
        existing = [{"host": "trino-a-w1", "address": "10.0.0.11",
                     "source": SOURCE_MANUAL, "reason": "imported"}]
        found = [{"cluster": "prod-a", "host": "10.0.0.11", "address": "10.0.0.11",
                  "role": "worker", "node_id": "w1", "version": "477",
                  "state": "active"}]
        plan = nodes.plan_refresh(existing, found)
        self.assertEqual(plan["added"], [])
        self.assertEqual(plan["silent"], [])
        # The alias survives: it is how Ansible resolves the host.
        self.assertEqual(plan["touched"][0]["host"], "trino-a-w1")

    def test_a_hand_entry_the_coordinator_confirms_becomes_discovered(self):
        existing = [{"host": "w1", "source": SOURCE_MANUAL, "address": "10.0.0.5",
                     "reason": "being rebuilt"}]
        change = nodes.plan_refresh(existing, self.found("w1"))["touched"][0]
        self.assertEqual(change["source"], SOURCE_DISCOVERED)
        self.assertIsNone(change["reason"])
        # The address somebody typed survives: it is how Ansible reaches it,
        # and discovery only knows the URI host.
        self.assertEqual(change["address"], "10.0.0.5")


class DescribeTest(unittest.TestCase):
    def test_the_newest_timestamp_in_a_cluster_dates_the_last_scan(self):
        now = utcnow()
        rows = nodes.describe_all([
            {"host": "w1", "source": SOURCE_DISCOVERED, "last_seen_at": now},
            {"host": "w2", "source": SOURCE_DISCOVERED,
             "last_seen_at": now - timedelta(days=2)},
            {"host": "w3", "source": SOURCE_MANUAL, "last_seen_at": None},
        ])
        self.assertEqual([r["answering"] for r in rows], [True, False, False])
        self.assertEqual([r["hand_entered"] for r in rows], [False, False, True])


class FakeSql:
    def __init__(self, rows):
        self.rows = rows

    def query(self, sql):
        return self.rows


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "prod-a.ini")
        self.repository = InMemoryNodeRepository()
        self.audit_rows = InMemoryAuditRepository()
        self.audit = AuditGuard(self.audit_rows)
        self.rows = [{"node_id": "c1", "http_uri": "https://10.0.0.1:8443",
                      "node_version": "477", "coordinator": True, "state": "active"},
                     {"node_id": "w1", "http_uri": "https://10.0.0.2:8443",
                      "node_version": "477", "coordinator": False, "state": "active"}]
        self.service = NodeListService(
            config=a_config(), repository=self.repository, audit_guard=self.audit,
            inventories={"prod-a": self.path},
            sql_client_factory=lambda cluster: FakeSql(self.rows))

    def test_a_scan_fills_the_list_and_writes_the_inventory(self):
        result = self.service.scan(principal(admin=True), "prod-a")
        self.assertEqual(sorted(result["added"]), ["10.0.0.1", "10.0.0.2"])
        parsed = parse_inventory(open(self.path).read(), "prod-a")
        self.assertEqual(sorted((n.host, n.role) for n in parsed),
                         [("10.0.0.1", "coordinator"), ("10.0.0.2", "worker")])

    def test_a_second_scan_that_loses_a_node_keeps_it_in_the_file(self):
        admin = principal(admin=True)
        self.service.scan(admin, "prod-a")
        self.rows = self.rows[:1]
        result = self.service.scan(admin, "prod-a")
        self.assertEqual(result["not_answering"], ["10.0.0.2"])
        self.assertIn("10.0.0.2", open(self.path).read())
        listed = self.service.overview(admin, "prod-a")
        self.assertEqual(listed["counts"]["silent"], 1)

    def test_adding_by_hand_requires_a_reason_and_is_audited(self):
        with self.assertRaises(Exception):
            self.service.add(principal(admin=True), "prod-a", "w9", "", "worker",
                             reason="")
        self.service.add(principal(admin=True), "prod-a", "w9", "10.0.0.9",
                         "worker", reason="down for a disk swap")
        self.assertEqual(self.audit_rows.records[-1].action_type,
                         ACTION_CLUSTER_NODE_CHANGE)
        self.assertIn("w9 ansible_host=10.0.0.9", open(self.path).read())

    def test_removing_a_node_takes_it_out_of_the_file(self):
        admin = principal(admin=True)
        self.service.add(admin, "prod-a", "w9", "", "worker", reason="gone")
        self.service.remove(admin, "prod-a", "w9", reason="decommissioned")
        self.assertNotIn("w9", open(self.path).read())
        with self.assertRaises(NotFound):
            self.service.remove(admin, "prod-a", "w9", reason="again")

    def test_a_viewer_cannot_change_the_list(self):
        with self.assertRaises(Forbidden):
            self.service.add(principal(), "prod-a", "w9", "", "worker", reason="x")
        with self.assertRaises(Forbidden):
            self.service.scan(principal(), "prod-a")

    def test_an_unknown_cluster_is_a_404(self):
        with self.assertRaises(NotFound):
            self.service.overview(principal(admin=True), "nowhere")

    def test_without_the_query_grant_the_scan_says_so(self):
        service = NodeListService(config=a_config(), repository=self.repository,
                                  audit_guard=self.audit,
                                  inventories={"prod-a": self.path})
        with self.assertRaises(InvalidRequest):
            service.scan(principal(admin=True), "prod-a")
        self.assertFalse(service.overview(principal(admin=True),
                                          "prod-a")["can_scan"])


if __name__ == "__main__":
    unittest.main()
