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


class InMemorySnapshotRepository(SnapshotRepository):
    """Used by tests and by a dry-run mode. Not for production."""

    def __init__(self) -> None:
        self.snapshots: Dict[str, Snapshot] = {}
        self.health_events: List[Dict[str, Any]] = []

    @staticmethod
    def _key(cluster: str, kind: str) -> str:
        return "{}::{}".format(cluster, kind)

    def save(self, snapshot: Snapshot) -> None:
        self.snapshots[self._key(snapshot.cluster, snapshot.kind)] = snapshot

    def load(self, cluster: str, kind: str) -> Optional[Snapshot]:
        return self.snapshots.get(self._key(cluster, kind))

    def record_health_events(self, events: List[Dict[str, Any]]) -> None:
        self.health_events.extend(events)
