"""Node inventory collection (FR-FL-01).

Joins two sources that each answer half the question:

* the **Ansible inventory** says which nodes are supposed to exist, and what
  role each one has. It is the only source that knows about a node that is
  switched off - a node TMS cannot reach is exactly the one worth showing.
* each node's own **`GET /v1/info`** says what it actually is right now:
  version, state, environment, uptime, whether it thinks it is the coordinator.
  Measured PUBLIC on 477, so this costs no credentials and no query slot.

Why not `system.runtime.nodes`
------------------------------
REQUIREMENTS names it the primary source. Measured on 2026-08-09 it needs the
`ExecuteQuery` permission, which the TMS service account deliberately does not
have - the request comes back `Access Denied: Cannot execute query`. Using it
would mean granting TMS the right to run SQL on production, widening what a
compromised TMS could do, in exchange for one field. That is a decision for the
platform team, not a collector implementation detail, so this module works
without it and the screen names what is consequently missing.

⛔ What this cannot tell you
---------------------------
Discovery join status per node (FR-FL-02) genuinely needs
`system.runtime.nodes`: the coordinator MBean exposes *counts*
(Active/Inactive/ShuttingDown/Draining/Drained) but never node identities. So
TMS can say "eleven of twelve workers have joined" and cannot say which one has
not. The count is collected and shown as a cross-check; the screen does not
pretend to the identity.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

from tms.clients.errors import TrinoClientError
from tms.clients.node import NodeClient
from tms.collector.snapshot import KIND_FLEET, Snapshot, utcnow

log = logging.getLogger(__name__)


def _node_record(node, info: Optional[Dict[str, Any]], error: Optional[str]) -> Dict[str, Any]:
    record = node.as_dict()
    record["reachable"] = info is not None
    record["error"] = error
    if info is None:
        # Deliberately no version/state guess. "Unknown" is the fact.
        record.update({"node_id": None, "state": None, "version": None,
                       "environment": None, "uptime": None, "coordinator": None,
                       "starting": None})
        return record

    version = info.get("nodeVersion")
    record.update({
        "node_id": info.get("nodeId"),
        "state": info.get("state"),
        "version": (version or {}).get("version") if isinstance(version, dict) else version,
        "environment": info.get("environment"),
        "uptime": info.get("uptime"),
        "coordinator": info.get("coordinator"),
        "starting": info.get("starting"),
    })
    return record


def _disagreements(records: List[Dict[str, Any]]) -> List[str]:
    """Things worth saying out loud about the fleet as a whole.

    All three are silent in per-node rows but obvious across them, and each one
    is a real incident shape rather than a cosmetic mismatch.
    """
    notes = []
    live = [r for r in records if r["reachable"]]

    versions = sorted({r["version"] for r in live if r["version"]})
    if len(versions) > 1:
        notes.append("Mixed Trino versions in this cluster: {}.".format(", ".join(versions)))

    environments = sorted({r["environment"] for r in live if r["environment"]})
    if len(environments) > 1:
        # Nodes with different `node.environment` never form one cluster; they
        # look up but are invisible to the coordinator.
        notes.append(
            "Nodes disagree about node.environment ({}). Nodes in different "
            "environments do not join the same cluster.".format(", ".join(environments)))

    claimed = [r for r in live if r.get("coordinator")]
    if len(claimed) > 1:
        notes.append(
            "More than one node reports being the coordinator: {}.".format(
                ", ".join(sorted(r["host"] for r in claimed))))

    inventory_coordinators = [r for r in records if r["role"] == "coordinator"]
    for record in inventory_coordinators:
        if record["reachable"] and record.get("coordinator") is False:
            notes.append(
                "{} is listed as the coordinator in the inventory but reports "
                "that it is a worker.".format(record["host"]))
    return notes


class FleetPoller:
    """One cluster's nodes, on its own schedule.

    Node membership changes on the timescale of a deployment, not a query, so
    this polls far less often than the query collector. Every node is contacted
    independently and a failure is recorded against that node only - one dead
    worker must not blank the other eleven.
    """

    kind = KIND_FLEET

    def __init__(self, cluster: str, nodes, repository, url_template: str,
                 transport_factory, interval: float = 60.0,
                 node_counts: Optional[Dict[str, Any]] = None,
                 verify_tls: bool = True) -> None:
        self.cluster = cluster
        self.nodes = list(nodes)
        self.repository = repository
        self.url_template = url_template
        self._transport_factory = transport_factory
        self.interval = interval
        self.verify_tls = verify_tls

    def url_for(self, node) -> str:
        return self.url_template.format(address=node.address, host=node.host)

    def _client(self, node) -> NodeClient:
        return NodeClient(self.url_for(node), self._transport_factory(),
                          verify_tls=self.verify_tls)

    def tick(self, node_counts: Optional[Dict[str, Any]] = None) -> Snapshot:
        """Contact every node once and write the snapshot.

        `node_counts` is the coordinator's own view (ActiveNodeCount and
        friends), passed in by the caller that already reads JMX rather than
        read a second time here.
        """
        records: List[Dict[str, Any]] = []
        for node in self.nodes:
            info, error = None, None
            try:
                info = self._client(node).info()
            except TrinoClientError as exc:
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 - one node must not stop the rest
                error = "{}: {}".format(type(exc).__name__, exc)
                log.warning("fleet poll failed for %s: %s", node.host, exc)
            records.append(_node_record(node, info, error))

        reachable = [r for r in records if r["reachable"]]
        payload = {
            "nodes": records,
            "summary": {
                "total": len(records),
                "reachable": len(reachable),
                "unreachable": len(records) - len(reachable),
                "workers": sum(1 for r in records if r["role"] == "worker"),
                "shutting_down": sum(
                    1 for r in reachable if r.get("state") == "SHUTTING_DOWN"),
            },
            # The coordinator's own count, for cross-checking against the
            # inventory. TMS cannot name which worker has not joined - see the
            # module docstring - so it reports the discrepancy, not an identity.
            "node_counts": dict(node_counts or {}),
            "notes": _disagreements(records),
            "inventory_size": len(self.nodes),
        }

        collection_error = None
        advice = None
        if not self.nodes:
            collection_error = "No nodes in the inventory for this cluster."
            advice = ("Check fleet.inventories - TMS reads the [coordinator] and "
                      "[workers] sections of the Ansible inventory file.")

        snapshot = Snapshot(
            cluster=self.cluster, kind=KIND_FLEET, collected_at=utcnow(),
            payload=payload, collection_error=collection_error, advice=advice,
        )
        self.repository.save(snapshot)
        return snapshot
