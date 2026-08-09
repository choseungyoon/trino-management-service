"""Reading the fleet and taking a worker out of it (FR-FL-01, FR-FL-03).

Graceful shutdown is deliberately *not* built as a multi-step sequence like the
cluster restart. It does not need one: Trino drains the worker itself, the
cluster stays up throughout, and there is no traffic to stop at the Gateway.
Wrapping it in a six-step ceremony would imply a danger that is not there, and
ceremony that is not earned gets clicked through.

What it does need, and has:

* a reason, an audit record and an administrator (CLAUDE.md rule 3),
* a refusal to shut down a coordinator,
* honesty about how long it takes.

⛔ Why the coordinator is refused
--------------------------------
There is no coordinator HA. Shutting the coordinator down does not drain a
node, it ends the cluster - every running query dies with it. That is the
restart sequence's job, where traffic is stopped first. A "shutdown" button
that also happened to work on coordinators would be a path around CLAUDE.md
rule 5, so this one refuses by role.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

from tms.api.errors import Forbidden, InvalidRequest, NotFound, UpstreamUnavailable
from tms.api.permissions import MANAGE_HEALTH, Principal
from tms.clients.errors import TrinoClientError
from tms.clients.node import NodeClient
from tms.collector.snapshot import KIND_FLEET
from tms.core.audit import ACTION_NODE_SHUTDOWN, TARGET_NODE, AuditGuard

log = logging.getLogger(__name__)


class FleetService:
    def __init__(self, config, snapshots, audit_guard: AuditGuard,
                 transport_factory, stale_threshold: float = 120.0) -> None:
        self.config = config
        self.snapshots = snapshots
        self.audit = audit_guard
        self._transport_factory = transport_factory
        self._stale_threshold = stale_threshold

    # ------------------------------------------------------------------ read

    def get_fleet(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        from tms.api.services import envelope, require
        from tms.api.permissions import VIEW_HEALTH

        require(principal, VIEW_HEALTH)
        self._cluster_or_404(cluster)
        enabled = self.config.fleet.enabled
        snapshot = self.snapshots.load(cluster, KIND_FLEET)
        if snapshot is None:
            return envelope(
                None,
                {"nodes": [], "summary": {}, "notes": [], "enabled": enabled,
                 "unavailable_reason": (
                     None if enabled else
                     "Fleet collection is off (fleet.enabled).")},
                self._stale_threshold,
            )
        payload = dict(snapshot.payload or {})
        payload["enabled"] = enabled
        if snapshot.collection_error:
            payload["unavailable_reason"] = snapshot.collection_error
            payload["advice"] = snapshot.advice
        payload["limits"] = self._limits(payload)
        return envelope(snapshot, payload, self._stale_threshold)

    @staticmethod
    def _limits(payload: Dict[str, Any]) -> List[str]:
        """What this screen cannot tell you, and why.

        Stated on the screen rather than left for someone to infer from an
        absent column. A monitoring screen that quietly omits a fact is read as
        that fact being fine.
        """
        limits = [
            "Discovery join status per node needs `system.runtime.nodes`, which "
            "requires the ExecuteQuery permission TMS does not hold. The "
            "coordinator's node counts below are the cross-check TMS can make.",
        ]
        counts = payload.get("node_counts") or {}
        active = counts.get("ActiveNodeCount")
        expected = payload.get("inventory_size")
        if active is not None and expected:
            limits.append(
                "The inventory lists {} node(s); the coordinator counts {} "
                "active.".format(expected, active))
        return limits

    # ----------------------------------------------------------------- write

    def shutdown_node(self, principal: Principal, cluster: str, host: str,
                      reason: str) -> Dict[str, Any]:
        """Ask one worker to drain and exit (FR-FL-03)."""
        if not principal.can(MANAGE_HEALTH):
            raise Forbidden("You do not have permission to shut down a node.")
        self._cluster_or_404(cluster)
        if not (reason or "").strip():
            raise InvalidRequest("A reason is required to shut down a node.")

        node = self._node_or_404(cluster, host)
        if node.get("role") != "worker":
            # Not a permission problem, a correctness one: see the module note.
            raise InvalidRequest(
                "{} is the coordinator. Shutting it down ends the cluster and "
                "kills every running query — it does not drain a node. Use the "
                "restart sequence, which stops traffic first.".format(host))
        if node.get("state") == "SHUTTING_DOWN":
            raise InvalidRequest("{} is already shutting down.".format(host))

        url = self._url_for(node)
        with self.audit.action(
            actor=principal.username, roles=principal.roles,
            action_type=ACTION_NODE_SHUTDOWN, target_kind=TARGET_NODE,
            target_id=host, target_cluster=cluster, reason=reason,
            actor_ip=principal.ip,
        ):
            client = NodeClient(
                url, self._transport_factory(),
                user=self.config.trino.user,
                password=self.config.trino.password.reveal(),
                verify_tls=self.config.trino.verify_tls,
                write_timeout=self.config.trino.write_timeout_seconds,
            )
            try:
                client.begin_shutdown()
            except TrinoClientError as exc:
                raise UpstreamUnavailable(str(exc))

        return {
            "host": host,
            "cluster": cluster,
            "accepted": True,
            # Said back explicitly, because the node will still be listed as
            # SHUTTING_DOWN for minutes and that looks stuck otherwise.
            "note": (
                "{} is draining. Trino waits one grace period, finishes its "
                "running tasks, waits another grace period, then exits — at "
                "least twice `shutdown.grace-period` (four minutes on the "
                "defaults). It stays listed until it goes.".format(host)),
        }

    # ----------------------------------------------------------------- inner

    def _cluster_or_404(self, cluster: str):
        try:
            return self.config.cluster(cluster)
        except KeyError:
            raise NotFound("Unknown cluster: {}".format(cluster))

    def _node_or_404(self, cluster: str, host: str) -> Dict[str, Any]:
        snapshot = self.snapshots.load(cluster, KIND_FLEET)
        for node in ((snapshot.payload if snapshot else {}) or {}).get("nodes", []):
            if node.get("host") == host:
                return node
        raise NotFound(
            "{} is not in the inventory for {}. TMS will not send a shutdown to "
            "a host it does not know.".format(host, cluster))

    def _url_for(self, node: Dict[str, Any]) -> str:
        template = self.config.fleet.node_url_template
        # The address comes from the inventory file, never from the request:
        # the request only names a host that must already be in that file.
        return template.format(address=node.get("address") or node.get("host"),
                               host=node.get("host"))
