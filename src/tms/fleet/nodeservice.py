"""Managing the node list from the console (D-019).

The list used to be an Ansible inventory somebody edited on the server. It is
now a table, and this is what may change it:

* a **scan** asks the coordinator (`system.runtime.nodes`) and folds the answer
  in - additions and refreshes only,
* a person **adds** a node the coordinator cannot see, because it is down and
  still has to receive configuration,
* a person **removes** one, with a reason, because it is gone for good.

⛔ Every change re-renders that cluster's inventory file. The file is how the
restart, the config scan and the catalog deploy learn which hosts exist - they
take a *path*, never a host name (D-009), and that property is why they get a
generated file rather than a generated command line. If the render fails, the
change is rejected: a table that has moved on while the file has not is the
split this decision existed to close.

Python 3.9 compatible.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from tms.api.errors import (
    AuditUnavailableError,
    Forbidden,
    InvalidRequest,
    NotFound,
    ReasonRequiredError,
    UpstreamUnavailable,
)
from tms.api.permissions import MANAGE_HEALTH, VIEW_HEALTH, Principal
from tms.clients.errors import TrinoClientError
from tms.core.audit import (
    ACTION_CLUSTER_NODE_CHANGE,
    TARGET_NODE_LIST,
    AuditGuard,
    AuditUnavailable,
    ReasonRequired,
)
from tms.fleet import nodes
from tms.fleet.nodestore import (
    SOURCE_DISCOVERED,
    SOURCE_MANUAL,
    DuplicateNode,
    NodeStoreUnavailable,
    utcnow,
)

log = logging.getLogger(__name__)


class NodeListService:
    def __init__(self, config, repository, audit_guard: AuditGuard,
                 inventories: Dict[str, str],
                 sql_client_factory=None) -> None:
        self.config = config
        self.repository = repository
        self.audit = audit_guard
        # {cluster: path}. The same map every executor was handed, so what is
        # written here is what gets deployed against.
        self.inventories = dict(inventories or {})
        # None until the ExecuteQuery grant is in place (D-012). The screen
        # then offers hand entry only, and says why.
        self._sql_client_factory = sql_client_factory

    # ------------------------------------------------------------- guards

    def _require_view(self, principal: Principal) -> None:
        if not principal.can(VIEW_HEALTH):
            raise Forbidden("You do not have permission to view the node list.")

    def _require_admin(self, principal: Principal) -> None:
        if not principal.can(MANAGE_HEALTH):
            raise Forbidden("Changing the node list is restricted to administrators.")

    def _cluster_or_404(self, cluster: str) -> str:
        try:
            self.config.cluster(cluster)
        except KeyError:
            raise NotFound("Unknown cluster: {}".format(cluster))
        path = self.inventories.get(cluster)
        if not path:
            raise InvalidRequest(
                "No inventory path is configured for {}, so TMS has nowhere to "
                "write the node list.".format(cluster))
        return path

    def _audited(self, principal, target_id, reason, cluster):
        try:
            return self.audit.action(
                actor=principal.username, roles=principal.roles,
                action_type=ACTION_CLUSTER_NODE_CHANGE,
                target_kind=TARGET_NODE_LIST, target_id=str(target_id),
                target_cluster=cluster, reason=reason, actor_ip=principal.ip)
        except ReasonRequired as exc:
            raise ReasonRequiredError(str(exc))
        except AuditUnavailable as exc:
            raise AuditUnavailableError(str(exc))

    def _rows(self, cluster: str) -> List[Dict[str, Any]]:
        try:
            return self.repository.list(cluster)
        except NodeStoreUnavailable as exc:
            raise UpstreamUnavailable(str(exc))

    # ------------------------------------------------------------ reading

    @property
    def discovery_available(self) -> bool:
        return self._sql_client_factory is not None

    def overview(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        self._require_view(principal)
        self._cluster_or_404(cluster)
        rows = nodes.describe_all(self._rows(cluster))
        return {
            "cluster": cluster,
            "nodes": rows,
            "counts": {
                "total": len(rows),
                "workers": sum(1 for r in rows if r["role"] == "worker"),
                # What the list is for: these still get every deployment.
                "silent": sum(1 for r in rows if not r["answering"]),
            },
            "can_scan": self.discovery_available,
            "inventory_path": self.inventories.get(cluster),
        }

    # ------------------------------------------------------------ writing

    def _write_inventory(self, cluster: str) -> str:
        """Re-render the file from the table. Raises if it cannot be written."""
        path = self.inventories[cluster]
        text = nodes.render_inventory(cluster, self._rows(cluster))
        directory = os.path.dirname(path)
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            # Written whole and moved into place: a playbook that starts while
            # this is half-written would target half a cluster.
            temporary = path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(temporary, path)
        except OSError as exc:
            raise UpstreamUnavailable(
                "The node list could not be written to {}: {}".format(path, exc))
        return path

    def add(self, principal: Principal, cluster: str, host: str, address: str,
            role: str, reason: str) -> Dict[str, Any]:
        """Add a node the coordinator cannot see.

        The only reason to type one in: it is down, so discovery cannot find
        it, and it still has to receive configuration. The reason is required
        because six months later "why is w9 in this list and not answering" has
        to have an answer other than a guess.
        """
        self._require_admin(principal)
        self._cluster_or_404(cluster)
        try:
            entry = nodes.validate(cluster, host, address, role,
                                   [c.name for c in self.config.clusters])
        except nodes.NodeError as exc:
            raise InvalidRequest(str(exc))

        # The whole write is inside the audit context, so a rejected host or a
        # file TMS cannot write is recorded as an attempt that failed rather
        # than leaving no trace at all.
        with self._audited(principal, "{}/{}".format(cluster, entry["host"]),
                           reason, cluster):
            try:
                row = self.repository.add(
                    cluster=entry["cluster"], host=entry["host"],
                    address=entry["address"], role=entry["role"],
                    source=SOURCE_MANUAL, actor=principal.username, reason=reason)
            except DuplicateNode:
                raise InvalidRequest(
                    "{} is already listed for {}.".format(entry["host"], cluster))
            except NodeStoreUnavailable as exc:
                raise UpstreamUnavailable(str(exc))
            self._write_inventory(cluster)
        return row

    def remove(self, principal: Principal, cluster: str, host: str,
               reason: str) -> Dict[str, Any]:
        """Take a node off the list.

        ⛔ This is the only way a node leaves, and it is not undone by a scan
        finding it again - a scan would re-add it as `discovered`, which is the
        right answer if it came back. What removal means is "stop deploying to
        this host", so it is a decision with a reason rather than a
        consequence of a node being briefly unreachable.
        """
        self._require_admin(principal)
        self._cluster_or_404(cluster)
        host = nodes.clean(host)
        with self._audited(principal, "{}/{}".format(cluster, host),
                           reason, cluster):
            try:
                removed = self.repository.remove(cluster, host)
            except NodeStoreUnavailable as exc:
                raise UpstreamUnavailable(str(exc))
            if not removed:
                raise NotFound("{} is not listed for {}.".format(host, cluster))
            self._write_inventory(cluster)
        return {"cluster": cluster, "host": host, "removed": True}

    def scan(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        """Ask the coordinator which nodes it can see, and fold the answer in.

        A read of the cluster and a write of TMS's own list. No reason and no
        audit row: it cannot remove anything, and what it adds it attributes to
        the scan rather than to a person. Rule 3 governs decisions, and this
        makes none - `plan_refresh` is where that is enforced.

        ⛔ On demand, never on a timer. This spends a Trino query slot, and the
        grant that allows it (D-012) holds only while these stay rare.
        """
        self._require_admin(principal)
        self._cluster_or_404(cluster)
        if not self.discovery_available:
            raise InvalidRequest(
                "TMS cannot query this cluster, so the node list cannot be "
                "scanned. Nodes can still be added by hand.")

        from tms.fleet.discovery import NODES_QUERY

        try:
            rows = self._sql_client_factory(cluster).query(NODES_QUERY)
        except TrinoClientError as exc:
            from tms.fleet.discovery import _advice_for

            raise UpstreamUnavailable(
                "{}{}".format(exc, " " + (_advice_for(exc) or "")).strip())

        found = nodes.from_discovery(rows, cluster)
        plan = nodes.plan_refresh(self._rows(cluster), found)
        # ⛔ One timestamp for the whole scan. `describe_all` reads the newest
        # one in a cluster as "when the last scan ran", so a row that was not
        # in this answer keeps an older one and shows as not answering. Calling
        # utcnow() per row would make every row look freshly seen.
        seen_at = utcnow()

        try:
            for node in plan["added"]:
                self.repository.add(
                    cluster=cluster, host=node["host"], address=node["address"],
                    role=node["role"], source=SOURCE_DISCOVERED,
                    actor="tms-scan", node_id=node["node_id"],
                    version=node["version"], last_seen_at=seen_at)
            for change in plan["touched"]:
                self.repository.touch(cluster, change.pop("host"),
                                      **{k: v for k, v in change.items()
                                         if k not in ("cluster", "address")},
                                      last_seen_at=seen_at)
        except NodeStoreUnavailable as exc:
            raise UpstreamUnavailable(str(exc))

        self._write_inventory(cluster)
        log.info("node scan on %s: %d added, %d refreshed, %d not answering",
                 cluster, len(plan["added"]), len(plan["touched"]),
                 len(plan["silent"]))
        return {
            "cluster": cluster,
            "added": [n["host"] for n in plan["added"]],
            "refreshed": len(plan["touched"]),
            # Named, not counted: these are the ones somebody has to decide about.
            "not_answering": [row["host"] for row in plan["silent"]],
            "scanned_at": seen_at.isoformat(),
        }
