"""Fleet inventory and graceful shutdown (FR-FL-01, FR-FL-03).

Most of what matters here is refusals: the shutdown write reaches production
nodes, so the tests are mostly about what it will not do.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.api.errors import Forbidden, InvalidRequest, NotFound, UpstreamUnavailable  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.clients.errors import TrinoClientError, TrinoForbidden  # noqa: E402
from tms.clients.node import NodeClient, NodeUnreachable  # noqa: E402
from tms.collector.fleet_poller import FleetPoller  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    KIND_FLEET,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.core.audit import (  # noqa: E402
    ACTION_NODE_SHUTDOWN,
    FAILURE,
    SUCCESS,
    AuditGuard,
    InMemoryAuditRepository,
)
from tms.core.config import build_config  # noqa: E402
from tms.fleet.inventory import Node, load_inventory, parse_inventory  # noqa: E402
from tms.fleet.service import FleetService  # noqa: E402

ADMIN = Principal("syhcho", ["admin"])
VIEWER = Principal("reader", ["viewer"])


class FakeResponse:
    def __init__(self, status=200, body=b"{}"):
        self.status = status
        self.body = body

    def text(self):
        return self.body.decode("utf-8")


class FakeTransport:
    """Records every request so the tests can assert on what was sent."""

    def __init__(self, responses=None, boom=None):
        self.responses = dict(responses or {})
        self.boom = boom
        self.requests = []

    def request(self, method, url, headers=None, body=None, connect_timeout=None,
                read_timeout=None, verify_tls=True):
        self.requests.append({"method": method, "url": url,
                              "headers": dict(headers or {}), "body": body})
        if self.boom:
            raise self.boom
        if url not in self.responses:
            if method == "GET":
                # An unregistered host is an unreachable one. Answering anyway
                # would make "the node is down" untestable - which is exactly
                # the case the fleet screen exists for.
                raise OSError("connection refused: {}".format(url))
            return FakeResponse()
        return self.responses[url]


# ─────────────────────────────────────────────────────────── inventory

class InventoryTest(unittest.TestCase):
    SAMPLE = """
# the cluster's nodes
[coordinator]
trino-coord-1 ansible_host=10.0.0.10

[workers]
trino-worker-1 ansible_host=10.0.0.11
trino-worker-2   # trailing comment
trino-worker-1 ansible_host=10.0.0.11

[workers:vars]
ansible_user=trino

[gateway]
gw-1

[cluster1:children]
coordinator
workers
"""

    def test_roles_come_from_the_sections(self):
        nodes = parse_inventory(self.SAMPLE, "prod-a")
        self.assertEqual(
            [("trino-coord-1", "coordinator"), ("trino-worker-1", "worker"),
             ("trino-worker-2", "worker")],
            [(n.host, n.role) for n in nodes])

    def test_ansible_host_wins_as_the_address(self):
        """The inventory alias is often a name only Ansible resolves; connecting
        to it fails in a way that looks like the node being down."""
        nodes = {n.host: n for n in parse_inventory(self.SAMPLE, "prod-a")}
        self.assertEqual("10.0.0.11", nodes["trino-worker-1"].address)
        self.assertEqual("trino-worker-2", nodes["trino-worker-2"].address)

    def test_unknown_groups_are_skipped_not_guessed(self):
        """A node with the wrong role attached is worse than a missing one:
        shutdown treats coordinator and worker very differently."""
        hosts = [n.host for n in parse_inventory(self.SAMPLE, "prod-a")]
        self.assertNotIn("gw-1", hosts)
        self.assertNotIn("coordinator", hosts, "the :children list is not hosts")
        self.assertNotIn("ansible_user=trino", hosts)

    def test_a_vars_block_is_not_a_host_list(self):
        nodes = parse_inventory("[workers:vars]\nansible_user=trino\n", "prod-a")
        self.assertEqual([], nodes)

    def test_duplicates_collapse(self):
        nodes = parse_inventory(self.SAMPLE, "prod-a")
        self.assertEqual(1, sum(1 for n in nodes if n.host == "trino-worker-1"))

    def test_a_missing_file_yields_no_nodes_rather_than_raising(self):
        """Taking the console down because one file moved removes the screen
        someone is using to find out what moved."""
        self.assertEqual([], load_inventory("/nonexistent/cluster9.ini", "prod-a"))


# ────────────────────────────────────────────────────────── node client

class NodeClientTest(unittest.TestCase):
    def test_reads_send_no_credentials(self):
        """`/v1/info` is PUBLIC (measured on 477). Posting a management
        password to every worker on every poll spreads it for nothing."""
        transport = FakeTransport({
            "https://w1:8443/v1/info": FakeResponse(body=b'{"nodeId":"w1"}')})
        client = NodeClient("https://w1:8443", transport, user="tms-svc",
                            password="secret")
        client.info()
        sent = transport.requests[0]["headers"]
        self.assertNotIn("Authorization", sent)
        self.assertNotIn("X-Trino-User", sent)

    def test_the_shutdown_body_is_a_json_string_with_its_quotes(self):
        transport = FakeTransport()
        NodeClient("https://w1:8443", transport, user="tms-svc",
                   password="pw").begin_shutdown()
        request = transport.requests[0]
        self.assertEqual("PUT", request["method"])
        self.assertEqual("https://w1:8443/v1/info/state", request["url"])
        self.assertEqual(b'"SHUTTING_DOWN"', request["body"])
        self.assertEqual("application/json", request["headers"]["Content-Type"])
        self.assertIn("Authorization", request["headers"])
        self.assertEqual("tms-svc", request["headers"]["X-Trino-User"])

    def test_403_says_which_permission_is_missing(self):
        """Trino answers "Management only resource", which does not say what to
        do. This is the failure an operator will actually hit first."""
        transport = FakeTransport({
            "https://w1:8443/v1/info/state": FakeResponse(status=403)})
        client = NodeClient("https://w1:8443", transport, user="tms-svc", password="pw")
        with self.assertRaises(TrinoForbidden) as caught:
            client.begin_shutdown()
        self.assertIn("WriteSystemInformation", str(caught.exception))
        self.assertIn("every worker", str(caught.exception))

    def test_an_unreachable_node_is_distinguishable_from_a_refusal(self):
        transport = FakeTransport(boom=OSError("connection refused"))
        client = NodeClient("https://w1:8443", transport)
        with self.assertRaises(NodeUnreachable):
            client.info()


# ──────────────────────────────────────────────────────────── poller

class FleetPollerTest(unittest.TestCase):
    def _poller(self, nodes, responses):
        """Every node gets its own transport, as in production. A host with no
        registered response is unreachable."""
        repository = InMemorySnapshotRepository()
        poller = FleetPoller(
            cluster="prod-a", nodes=nodes, repository=repository,
            url_template="https://{address}:8443",
            transport_factory=lambda: FakeTransport(responses), interval=60.0)
        return poller, repository

    @staticmethod
    def _info(node_id, version="477", environment="prod", state="ACTIVE",
              coordinator=False):
        import json

        return FakeResponse(body=json.dumps({
            "nodeId": node_id, "state": state, "nodeVersion": {"version": version},
            "environment": environment, "coordinator": coordinator,
            "uptime": "3.00d", "starting": False,
        }).encode("utf-8"))

    def test_one_dead_node_does_not_blank_the_others(self):
        nodes = [Node("w1", "worker", "prod-a"), Node("w2", "worker", "prod-a")]
        responses = {"https://w1:8443/v1/info": self._info("w1")}   # w2 has none
        poller, _repo = self._poller(nodes, responses)
        snapshot = poller.tick()

        rows = {n["host"]: n for n in snapshot.payload["nodes"]}
        self.assertTrue(rows["w1"]["reachable"])
        self.assertEqual("477", rows["w1"]["version"])
        self.assertFalse(rows["w2"]["reachable"])
        self.assertEqual(1, snapshot.payload["summary"]["unreachable"])

    def test_an_unreachable_node_reports_no_state_rather_than_a_guess(self):
        poller, _repo = self._poller([Node("w1", "worker", "prod-a")], {})
        row = poller.tick().payload["nodes"][0]
        self.assertIsNone(row["state"])
        self.assertIsNone(row["version"])
        self.assertIsNotNone(row["error"])

    def test_mixed_versions_are_called_out(self):
        nodes = [Node("w1", "worker", "prod-a"), Node("w2", "worker", "prod-a")]
        poller, _repo = self._poller(nodes, {
            "https://w1:8443/v1/info": self._info("w1", version="477"),
            "https://w2:8443/v1/info": self._info("w2", version="470"),
        })
        notes = " ".join(poller.tick().payload["notes"])
        self.assertIn("Mixed Trino versions", notes)

    def test_an_environment_split_is_called_out(self):
        """Nodes with different node.environment never join the same cluster;
        they look up and are invisible to the coordinator."""
        nodes = [Node("w1", "worker", "prod-a"), Node("w2", "worker", "prod-a")]
        poller, _repo = self._poller(nodes, {
            "https://w1:8443/v1/info": self._info("w1", environment="prod"),
            "https://w2:8443/v1/info": self._info("w2", environment="staging"),
        })
        notes = " ".join(poller.tick().payload["notes"])
        self.assertIn("node.environment", notes)

    def test_a_coordinator_that_reports_itself_as_a_worker_is_called_out(self):
        nodes = [Node("c1", "coordinator", "prod-a")]
        poller, _repo = self._poller(nodes, {
            "https://c1:8443/v1/info": self._info("c1", coordinator=False)})
        notes = " ".join(poller.tick().payload["notes"])
        self.assertIn("listed as the coordinator", notes)

    def test_an_empty_inventory_is_an_explained_failure(self):
        poller, _repo = self._poller([], {})
        snapshot = poller.tick()
        self.assertIsNotNone(snapshot.collection_error)
        self.assertIn("fleet.inventories", snapshot.advice)


# ─────────────────────────────────────────────────────────── service

def build_fleet_service(nodes=None, roles=("admin",)):
    config = build_config({
        "clusters": [{"name": "prod-a", "coordinator_url": "https://a:8443",
                      "expected_workers": 2}],
        "trino": {"user": "tms-svc", "password": "pw", "verify_tls": False},
        "database": {"url": "postgresql://u:p@h:5432/d"},
        "collector": {"stale_threshold_seconds": 600},
        "fleet": {"enabled": True, "inventories": {"prod-a": "/etc/tms/c1.ini"},
                  "node_url_template": "https://{address}:8443"},
    })
    snapshots = InMemorySnapshotRepository()
    if nodes is not None:
        snapshots.save(Snapshot("prod-a", KIND_FLEET, utcnow(), payload={
            "nodes": nodes, "summary": {}, "notes": [], "node_counts": {},
            "inventory_size": len(nodes),
        }))
    audit = InMemoryAuditRepository()
    transport = FakeTransport()
    service = FleetService(config=config, snapshots=snapshots,
                           audit_guard=AuditGuard(audit),
                           transport_factory=lambda: transport)
    return service, audit, transport


def node_row(host, role="worker", state="ACTIVE", address=None):
    return {"host": host, "address": address or host, "role": role,
            "cluster": "prod-a", "reachable": True, "state": state,
            "version": "477", "environment": "prod", "uptime": "1d",
            "coordinator": role == "coordinator", "error": None}


class ShutdownTest(unittest.TestCase):
    def test_the_coordinator_is_refused(self):
        """No coordinator HA: shutting it down ends the cluster and kills every
        running query. That is the restart sequence's job, where traffic is
        stopped first - so this must not be a path around CLAUDE.md rule 5."""
        service, _audit, transport = build_fleet_service([node_row("c1", role="coordinator")])
        with self.assertRaises(InvalidRequest) as caught:
            service.shutdown_node(ADMIN, "prod-a", "c1", reason="scaling down")
        self.assertIn("ends the cluster", str(caught.exception))
        self.assertEqual([], transport.requests, "nothing was sent")

    def test_a_reason_is_required(self):
        service, _audit, transport = build_fleet_service([node_row("w1")])
        with self.assertRaises(InvalidRequest):
            service.shutdown_node(ADMIN, "prod-a", "w1", reason="   ")
        self.assertEqual([], transport.requests)

    def test_a_viewer_cannot_shut_a_node_down(self):
        service, _audit, transport = build_fleet_service([node_row("w1")])
        with self.assertRaises(Forbidden):
            service.shutdown_node(VIEWER, "prod-a", "w1", reason="scaling down")
        self.assertEqual([], transport.requests)

    def test_a_host_not_in_the_inventory_is_refused(self):
        """The request names a host; the address comes from the inventory. A
        host TMS has never heard of gets no request at all."""
        service, _audit, transport = build_fleet_service([node_row("w1")])
        with self.assertRaises(NotFound):
            service.shutdown_node(ADMIN, "prod-a", "evil.example", reason="x")
        self.assertEqual([], transport.requests)

    def test_the_url_comes_from_the_inventory_not_the_request(self):
        service, _audit, transport = build_fleet_service(
            [node_row("w1", address="10.0.0.11")])
        service.shutdown_node(ADMIN, "prod-a", "w1", reason="scaling down")
        self.assertEqual("https://10.0.0.11:8443/v1/info/state",
                         transport.requests[0]["url"])

    def test_shutting_down_twice_is_refused(self):
        service, _audit, transport = build_fleet_service(
            [node_row("w1", state="SHUTTING_DOWN")])
        with self.assertRaises(InvalidRequest):
            service.shutdown_node(ADMIN, "prod-a", "w1", reason="again")
        self.assertEqual([], transport.requests)

    def test_a_successful_shutdown_is_audited_with_the_reason(self):
        service, audit, _transport = build_fleet_service([node_row("w1")])
        service.shutdown_node(ADMIN, "prod-a", "w1", reason="scaling down for CHG-1")
        record = audit.records[-1]
        self.assertEqual(ACTION_NODE_SHUTDOWN, record.action_type)
        self.assertEqual("w1", record.target_id)
        self.assertEqual("scaling down for CHG-1", record.reason)
        self.assertEqual(SUCCESS, record.outcome)

    def test_a_refused_shutdown_is_still_audited(self):
        """The attempt happened. An action that failed at the node is still an
        action someone took against production."""
        service, audit, transport = build_fleet_service([node_row("w1")])
        transport.responses["https://w1:8443/v1/info/state"] = FakeResponse(status=403)
        with self.assertRaises(UpstreamUnavailable):
            service.shutdown_node(ADMIN, "prod-a", "w1", reason="scaling down")
        record = audit.records[-1]
        self.assertEqual(ACTION_NODE_SHUTDOWN, record.action_type)
        self.assertEqual(FAILURE, record.outcome)

    def test_the_reply_says_how_long_it_will_take(self):
        """The node stays listed as draining for minutes. Without saying so,
        that reads as stuck and invites someone to kill the process."""
        service, _audit, _transport = build_fleet_service([node_row("w1")])
        note = service.shutdown_node(ADMIN, "prod-a", "w1", reason="x")["note"]
        self.assertIn("grace-period", note)
        self.assertIn("stays listed", note)


class FleetReadTest(unittest.TestCase):
    def test_the_screen_names_what_it_cannot_know(self):
        """Discovery join per node needs ExecuteQuery, which TMS does not hold.
        An omitted fact reads as a fact that is fine."""
        service, _audit, _transport = build_fleet_service([node_row("w1")])
        limits = " ".join(service.get_fleet(ADMIN, "prod-a")["data"]["limits"])
        self.assertIn("system.runtime.nodes", limits)
        self.assertIn("ExecuteQuery", limits)

    def test_an_unknown_cluster_is_a_404(self):
        service, _audit, _transport = build_fleet_service([node_row("w1")])
        with self.assertRaises(NotFound):
            service.get_fleet(ADMIN, "nope")


if __name__ == "__main__":
    unittest.main()
