"""Bridge between polling (V4) and health evaluation (V5).

Health evaluation runs in the collector, not in the API, because the engine
carries state across evaluations: the cumulative OOM counter and the
stabilisation counts. Evaluating in the API would mean every replica keeps its
own counters, so a state transition would be recorded once per replica and a
spike would be smoothed differently on each one.

The collector is already the single instance (advisory lock), so it is the only
place that state can live coherently. The API reads the resulting `health`
snapshot.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

from tms.collector.snapshot import (
    KIND_HEALTH,
    KIND_INFO,
    KIND_JMX,
    KIND_QUERIES,
    Snapshot,
    SnapshotRepository,
    utcnow,
)
from tms.health.engine import HealthEngine
from tms.health.tests import HealthContext

log = logging.getLogger(__name__)


class HealthWriter:
    def __init__(
        self,
        engine: HealthEngine,
        repository: SnapshotRepository,
        stale_threshold_seconds: float = 30.0,
        thresholds: Optional[Dict[str, float]] = None,
        coordinator_counted_in_active_nodes: bool = True,
    ) -> None:
        self.engine = engine
        self.repository = repository
        self.stale_threshold_seconds = stale_threshold_seconds
        self.thresholds = thresholds or {}
        self.coordinator_counted_in_active_nodes = coordinator_counted_in_active_nodes

    def evaluate(
        self,
        cluster_name: str,
        expected_workers: int,
        gateway_backends: Optional[List[Dict[str, Any]]] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Snapshot:
        """Evaluate health from the stored snapshots and persist the result."""
        now = utcnow()
        info = self.repository.load(cluster_name, KIND_INFO)
        jmx = self.repository.load(cluster_name, KIND_JMX)
        queries = self.repository.load(cluster_name, KIND_QUERIES)

        # Any input being stale makes the whole verdict stale: a health page
        # assembled from a fresh reading and a ten-minute-old one is not fresh.
        inputs = [s for s in (info, jmx, queries) if s is not None]
        stale = not inputs or any(
            s.is_stale(now, self.stale_threshold_seconds) for s in inputs
        )

        context = HealthContext(
            cluster_name=cluster_name,
            expected_workers=expected_workers,
            thresholds=self.thresholds,
            info=info,
            jmx=jmx,
            queries=queries,
            coordinator_counted_in_active_nodes=self.coordinator_counted_in_active_nodes,
            gateway_backends=gateway_backends,
        )

        health = self.engine.evaluate(context, now, stale=stale, overrides=overrides)

        events = self.engine.confirm_transitions(health)
        if events:
            try:
                self.repository.record_health_events(events)
            except Exception:  # noqa: BLE001 - losing an event must not stop polling
                log.exception("failed to record %d health event(s)", len(events))

        snapshot = Snapshot(
            cluster=cluster_name,
            kind=KIND_HEALTH,
            collected_at=now,
            payload=health.as_dict(),
        )
        try:
            self.repository.save(snapshot)
        except Exception:  # noqa: BLE001
            log.exception("failed to persist health snapshot for %s", cluster_name)
        return snapshot
