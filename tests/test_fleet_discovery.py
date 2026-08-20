"""Naming the node that did not join discovery (FR-FL-02, D-012).

TMS could only ever say *how many* nodes were missing: the coordinator's MBean
gives a count with no identifiers and `GET /v1/node` does not exist in 477. The
gap mattered because a node can answer `/v1/info` perfectly and still be
invisible to the coordinator - so "it is reachable" was never the same question
as "it joined".
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.api.errors import InvalidRequest  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.clients.errors import TrinoClientError  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    KIND_FLEET,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.core.audit import AuditGuard, InMemoryAuditRepository  # noqa: E402
from tms.core.config import build_config  # noqa: E402
from tms.fleet.discovery import compare, host_of, identify  # noqa: E402
from tms.fleet.service import FleetService  # noqa: E402

VIEWER = Principal("watcher", ["viewer"], ip="10.0.0.9")

INVENTORY = [
    {"host": "trino-a-c1", "address": "10.0.0.10", "role": "coordinator"},
    {"host": "trino-a-w1", "address": "10.0.0.11", "role": "worker"},
    {"host": "trino-a-w2", "address": "10.0.0.12", "role": "worker"},
]


def node(uri, node_id="n", state="active"):
    return {"node_id": node_id, "http_uri": uri, "node_version": "477",
            "coordinator": False, "state": state}


class HostTest(unittest.TestCase):
    def test_a_host_is_extracted_from_whatever_shape_the_uri_has(self):
        for uri in ("https://10.0.0.11:8443", "http://10.0.0.11:8080",
                    "10.0.0.11:8443", "10.0.0.11"):
            self.assertEqual("10.0.0.11", host_of(uri), uri)

    def test_case_is_normalised_so_a_hostname_cannot_look_missing(self):
        self.assertEqual("trino-a-w1", host_of("https://TRINO-A-W1:8443"))

    def test_nothing_in_gives_nothing_out(self):
        self.assertEqual("", host_of(""))
        self.assertEqual("", host_of(None))


class CompareTest(unittest.TestCase):
    def test_the_inventory_node_with_no_matching_uri_is_named(self):
        result = compare(INVENTORY, [
            node("https://10.0.0.10:8443"), node("https://10.0.0.11:8443")])
        self.assertEqual(["trino-a-w2"], [n["host"] for n in result["unjoined"]])

    def test_a_full_house_reports_nothing_missing(self):
        result = compare(INVENTORY, [node("https://10.0.0.1{}:8443".format(i))
                                     for i in (0, 1, 2)])
        self.assertEqual([], result["unjoined"])
        self.assertEqual([], result["unexpected"])

    def test_scheme_and_port_differences_do_not_invent_a_missing_node(self):
        """Both sides are reduced to a hostname before comparing."""
        result = compare(INVENTORY, [node("http://10.0.0.10:8080"),
                                     node("10.0.0.11"),
                                     node("https://10.0.0.12:8443")])
        self.assertEqual([], result["unjoined"])

    def test_a_node_can_be_matched_by_hostname_instead_of_address(self):
        result = compare(INVENTORY, [node("https://trino-a-c1:8443"),
                                     node("https://trino-a-w1:8443"),
                                     node("https://trino-a-w2:8443")])
        self.assertEqual([], result["unjoined"])

    def test_a_node_serving_queries_but_absent_from_the_inventory_is_flagged(self):
        """Rarer than the other direction and more alarming: the platform team
        does not know this node exists."""
        result = compare(INVENTORY, [node("https://10.0.0.1{}:8443".format(i))
                                     for i in (0, 1, 2)]
                         + [node("https://10.0.0.99:8443", node_id="stranger")])
        self.assertEqual(["stranger"], [r["node_id"] for r in result["unexpected"]])

    def test_an_empty_inventory_does_not_claim_every_node_is_a_stranger(self):
        result = compare([], [node("https://10.0.0.11:8443")])
        self.assertEqual([], result["unjoined"])
        self.assertEqual(1, len(result["unexpected"]))


class StubSql:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.queries = []

    def query(self, statement):
        self.queries.append(statement)
        if self.error:
            raise self.error
        return self.rows


class IdentifyTest(unittest.TestCase):
    def test_the_query_only_touches_system_runtime_nodes(self):
        sql = StubSql([node("https://10.0.0.10:8443")])
        identify(sql, INVENTORY)
        self.assertIn("system.runtime.nodes", sql.queries[0])
        self.assertEqual(1, len(sql.queries), "one query, not one per node")

    def test_a_failure_comes_back_as_a_message_not_an_exception(self):
        """This feeds a screen someone opened during an incident."""
        result = identify(StubSql(error=TrinoClientError("connection refused")),
                          INVENTORY)
        self.assertFalse(result["available"])
        self.assertIn("connection refused", result["error"])

    def test_a_permission_denial_names_the_fix(self):
        result = identify(
            StubSql(error=TrinoClientError("PERMISSION_DENIED: Cannot execute query")),
            INVENTORY)
        self.assertIn("execute", result["advice"])
        self.assertIn("tms-svc", result["advice"])


def build(sql=None):
    config = build_config({
        "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid",
                      "expected_workers": 11}],
        "trino": {"user": "u", "password": "p"},
        "database": {"url": "postgresql://u:p@h/d"},
        "fleet": {"enabled": True, "inventories": {"prod-a": __file__},
                  "node_url_template": "https://{address}:8443"},
    })
    snapshots = InMemorySnapshotRepository()
    snapshots.save(Snapshot("prod-a", KIND_FLEET, utcnow(),
                            payload={"nodes": INVENTORY, "inventory_size": 3,
                                     "node_counts": {"ActiveNodeCount": 2}}))
    audit = InMemoryAuditRepository()
    return FleetService(
        config=config, snapshots=snapshots, audit_guard=AuditGuard(audit),
        transport_factory=lambda: None,
        sql_client_factory=(lambda cluster: sql) if sql else None)


class ServiceTest(unittest.TestCase):
    def test_the_lookup_reads_the_inventory_from_the_snapshot(self):
        sql = StubSql([node("https://10.0.0.10:8443"), node("https://10.0.0.11:8443")])
        result = build(sql).identify_unjoined(VIEWER, "prod-a")
        self.assertEqual(["trino-a-w2"], [n["host"] for n in result["unjoined"]])

    def test_without_the_permission_wiring_the_screen_is_told_why(self):
        with self.assertRaises(InvalidRequest) as caught:
            build(sql=None).identify_unjoined(VIEWER, "prod-a")
        self.assertIn("ExecuteQuery", str(caught.exception))

    def test_a_viewer_may_run_it_because_it_reads(self):
        """Rule 3 governs writes. This changes nothing on the cluster."""
        sql = StubSql([node("https://10.0.0.10:8443")])
        self.assertTrue(build(sql).identify_unjoined(VIEWER, "prod-a")["available"])

    def test_nothing_runs_until_someone_asks(self):
        """⛔ The whole basis of D-012: these queries stay rare because no
        timer issues them."""
        sql = StubSql([])
        build(sql)
        self.assertEqual([], sql.queries)


if __name__ == "__main__":
    unittest.main()
