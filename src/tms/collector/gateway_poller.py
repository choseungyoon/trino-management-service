"""Gateway polling (FR-GATEWAY).

Fleet-level, not per-cluster: there is one Gateway deployment behind a load
balancer, so this runs once per tick rather than once per cluster.

What makes this screen worth more than the Gateway's own UI is the join with
what TMS monitors, and specifically the two disagreements it can surface:

* a Gateway backend TMS does not monitor — queries are being routed somewhere
  nobody is watching;
* a TMS cluster with no Gateway backend — TMS is watching something that
  receives no traffic.

Both are silent today. Each side looks correct on its own.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

from tms.clients.errors import TrinoClientError
from tms.collector.snapshot import GATEWAY_SCOPE, KIND_GATEWAY, Snapshot, utcnow

log = logging.getLogger(__name__)


def _normalise(url: Any) -> str:
    """Compare coordinator URLs without tripping over a trailing slash."""
    return str(url or "").rstrip("/").lower()


def join_backends(
    backends: List[Dict[str, Any]], clusters: List[Any]
) -> Dict[str, Any]:
    """Match Gateway backends to configured TMS clusters.

    Matching is by `proxyTo` against `coordinator_url` first, and only then by
    name. Names are chosen independently on each side and drift; the URL is the
    thing that actually determines where a query lands.
    """
    by_url = {}
    by_name = {}
    for cluster in clusters or []:
        by_url[_normalise(getattr(cluster, "coordinator_url", None))] = cluster.name
        by_name[str(cluster.name).lower()] = cluster.name

    rows = []
    matched_clusters = set()
    for backend in backends:
        name = backend.get("name")
        cluster_name = by_url.get(_normalise(backend.get("proxyTo")))
        matched_by = "url" if cluster_name else None
        if cluster_name is None:
            cluster_name = by_name.get(str(name or "").lower())
            matched_by = "name" if cluster_name else None
        if cluster_name:
            matched_clusters.add(cluster_name)
        rows.append({
            "name": name,
            "proxy_to": backend.get("proxyTo"),
            "external_url": backend.get("externalUrl"),
            "routing_group": backend.get("routingGroup") or "",
            "active": bool(backend.get("active")),
            "cluster": cluster_name,
            "matched_by": matched_by,
        })

    unmonitored = [r for r in rows if r["cluster"] is None]
    unrouted = [c.name for c in (clusters or []) if c.name not in matched_clusters]

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["routing_group"], []).append(row)

    return {
        "backends": rows,
        "groups": [
            {"name": name or "(none)", "backends": members,
             "active": sum(1 for m in members if m["active"]),
             "total": len(members)}
            for name, members in sorted(groups.items())
        ],
        "unmonitored_backends": [r["name"] for r in unmonitored],
        "unrouted_clusters": unrouted,
        "inactive_backends": [r["name"] for r in rows if not r["active"]],
    }


class GatewayPoller:
    """Polls the Gateway on its own schedule."""

    def __init__(self, client, repository, clusters=None, interval: float = 30.0,
                 clock=None) -> None:
        self.client = client
        self.repository = repository
        self.clusters = clusters or []
        self.interval = interval
        if clock is None:
            import time

            clock = time.monotonic
        self._clock = clock
        self._next_due = 0.0
        self.last_success: Optional[float] = None

    def is_due(self) -> bool:
        return self._clock() >= self._next_due

    def seconds_until_due(self) -> float:
        return max(0.0, self._next_due - self._clock())

    def poll(self) -> Snapshot:
        try:
            backends = self.client.list_backends()
        except TrinoClientError as exc:
            log.warning("gateway poll failed: %s", exc)
            self._next_due = self._clock() + self.interval
            return Snapshot(
                cluster=GATEWAY_SCOPE, kind=KIND_GATEWAY, collected_at=utcnow(),
                payload={}, collection_error="{}: {}".format(type(exc).__name__, exc),
                advice=exc.advice or None,
            )

        payload = join_backends(backends, self.clusters)
        # Rules are optional: the endpoint is undocumented and 500s when the
        # Gateway has none configured. Absent is a state, not a failure.
        payload["routing_rules"] = self.client.get_routing_rules()
        payload["live"] = self.client.is_live()

        self.last_success = self._clock()
        self._next_due = self._clock() + self.interval
        return Snapshot(
            cluster=GATEWAY_SCOPE, kind=KIND_GATEWAY, collected_at=utcnow(),
            payload=payload,
        )

    def tick(self) -> List[Snapshot]:
        if not self.is_due():
            return []
        snapshot = self.poll()
        try:
            self.repository.save(snapshot)
        except Exception:  # noqa: BLE001 - storage must not kill the loop
            log.exception("failed to persist gateway snapshot")
        return [snapshot]
