"""Snapshot types and the repository interface the collector writes through.

A snapshot always carries `collected_at` and may carry `collection_error`. The
pair exists so the API can tell three situations apart that otherwise look
identical in a UI:

* fresh data                      -> show it
* stale data (collector stopped)  -> show it, badged, never as current
* collected but untrustworthy     -> UNKNOWN plus the reason

The third case is the one that bites. With `file` access control a denied
`queries` rule filters the list to empty instead of returning 403, so "no rows"
and "not allowed to see the rows" arrive as the same response
(ARCHITECTURE.md 6-3-2).

Python 3.9 compatible.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

KIND_QUERIES = "queries"
KIND_JMX = "jmx"
KIND_INFO = "info"
KIND_HEALTH = "health"
KIND_RESOURCE_GROUPS = "resource_groups"
# Fleet-level, not per-cluster: there is one Gateway deployment behind a LB.
KIND_GATEWAY = "gateway"
GATEWAY_SCOPE = "*"

# Per-node facts from each node's own /v1/info (FR-FL-01). Cluster-scoped, so
# one row holds the whole fleet for that cluster.
KIND_FLEET = "fleet"

#: What each node has in its `etc/` directory, collected on request
#: rather than on a timer - a scan opens SSH to every node (D-018).
KIND_CONFIG = "config"

# ⛔ Mirrored by the CHECK constraint on collector_snapshot.kind. A kind added
# here without a migration is rejected by the database, and the collector logs
# it and carries on - so the screen simply stays empty forever with nothing
# obviously wrong. That has happened once already (migration 003).
# `tests/test_collector_units.py` compares the two.
ALLOWED_KINDS = (
    KIND_QUERIES, KIND_JMX, KIND_INFO, KIND_HEALTH,
    KIND_RESOURCE_GROUPS, KIND_GATEWAY, KIND_FLEET, KIND_CONFIG,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Snapshot:
    cluster: str
    kind: str
    collected_at: datetime
    payload: Dict[str, Any] = field(default_factory=dict)
    # Non-null means "we reached the coordinator but cannot trust this".
    collection_error: Optional[str] = None
    # Rendered to the operator when collection_error is set.
    advice: Optional[str] = None

    @property
    def trustworthy(self) -> bool:
        return self.collection_error is None

    def is_stale(self, now: datetime, threshold_seconds: float) -> bool:
        return (now - self.collected_at).total_seconds() > threshold_seconds


class SnapshotRepository:
    """Storage interface. Implemented by PostgreSQL in production and by an
    in-memory fake in tests, so the polling logic is testable without a database.
    """

    def save(self, snapshot: Snapshot) -> None:
        raise NotImplementedError

    def load(self, cluster: str, kind: str) -> Optional[Snapshot]:
        raise NotImplementedError

    def record_health_events(self, events: List[Dict[str, Any]]) -> None:
        raise NotImplementedError

    def save_health_override(
        self,
        cluster: str,
        test_id: str,
        enabled: Optional[bool],
        thresholds: Optional[Dict[str, Any]],
        updated_by: str,
    ) -> None:
        """Persist an operator override (FR-CH-03/04/05).

        `test_id` of "*" addresses the roll-up rather than a single test.
        """
        raise NotImplementedError

    def load_health_overrides(self, cluster: str) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError

    def list_health_events(self, cluster: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Confirmed transitions, newest first (FR-CH-07 read side)."""
        raise NotImplementedError


class InMemorySnapshotRepository(SnapshotRepository):
    """Used by tests and by a dry-run mode. Not for production."""

    def __init__(self) -> None:
        self.snapshots: Dict[str, Snapshot] = {}
        self.health_events: List[Dict[str, Any]] = []
        self.overrides: Dict[str, Dict[str, Dict[str, Any]]] = {}

    @staticmethod
    def _key(cluster: str, kind: str) -> str:
        return "{}::{}".format(cluster, kind)

    def save(self, snapshot: Snapshot) -> None:
        self.snapshots[self._key(snapshot.cluster, snapshot.kind)] = snapshot

    def load(self, cluster: str, kind: str) -> Optional[Snapshot]:
        return self.snapshots.get(self._key(cluster, kind))

    def record_health_events(self, events: List[Dict[str, Any]]) -> None:
        self.health_events.extend(events)

    def save_health_override(
        self,
        cluster: str,
        test_id: str,
        enabled: Optional[bool],
        thresholds: Optional[Dict[str, Any]],
        updated_by: str,
    ) -> None:
        entry = self.overrides.setdefault(cluster, {}).setdefault(test_id, {})
        if enabled is not None:
            entry["enabled"] = enabled
        if thresholds:
            entry["thresholds"] = dict(thresholds)
        entry["updated_by"] = updated_by

    def load_health_overrides(self, cluster: str) -> Dict[str, Dict[str, Any]]:
        return dict(self.overrides.get(cluster, {}))

    def list_health_events(self, cluster: str, limit: int = 20) -> List[Dict[str, Any]]:
        matching = [e for e in self.health_events if e.get("cluster") == cluster]
        return list(reversed(matching))[:limit]
