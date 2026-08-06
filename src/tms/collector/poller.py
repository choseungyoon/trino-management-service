"""Per-cluster polling logic.

Deliberately free of I/O beyond the injected client and repository, and driven
by an injected clock, so the whole schedule and every failure path can be tested
without a network or a database.

Three behaviours here are not obvious and exist for specific reasons:

1. A failure never propagates. One unreachable coordinator degrades its own
   cluster to UNKNOWN and leaves the other clusters polling (NFR-DEGRADE).
2. Empty query list plus RunningQueries > 0 is treated as a permission problem,
   not as an idle cluster. With `file` access control a denied `queries` rule
   filters the response to empty rather than returning 403, so without this
   cross-check TMS would confidently report "0 running queries" on a busy
   cluster (health test H-09). The cross-check only trusts a recent JMX
   reading: queries are polled before JMX, so an unbounded one would compare
   today's empty list against a RunningQueries count from a previous run.
3. Oversized responses raise the poll interval instead of hammering the
   coordinator. Peak concurrency is still unmeasured (WORKLOAD_PROFILE.md W2),
   so the collector adapts rather than trusting a guess.

Python 3.9 compatible.
"""

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from tms.clients.errors import TrinoClientError
from tms.clients.trino import NODE_MANAGER_MBEAN, TrinoClient
from tms.collector.snapshot import (
    KIND_INFO,
    KIND_JMX,
    KIND_QUERIES,
    Snapshot,
    SnapshotRepository,
    utcnow,
)
from tms.collector.units import parse_data_size_bytes, parse_duration_ms, truncate_utf8

log = logging.getLogger(__name__)

# MBeans read on every JMX poll. Names verified live on 2026-08-06; the
# FailureDetector MBean the 477 docs advertise does not exist in 477.
JMX_MBEANS = (
    NODE_MANAGER_MBEAN,
    "java.lang:type=Memory",
    "trino.execution:name=QueryManager",
    "trino.memory:name=ClusterMemoryManager",
)

_RUNNING_STATES = ("RUNNING", "FINISHING")
_QUEUED_STATES = ("QUEUED", "WAITING_FOR_RESOURCES")


def _get(mapping: Any, *path: str) -> Any:
    current = mapping
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def summarise_query(
    raw: Dict[str, Any], query_text_max_bytes: int, long_running_seconds: float
) -> Dict[str, Any]:
    """Reduce one BasicQueryInfo to the fields the live-query screen needs.

    Field names verified against BasicQueryInfo / BasicQueryStats @477.
    Durations arrive as strings ("1.98s") and sizes as byte strings
    ("8589934592B") - see collector.units.
    """
    stats = raw.get("queryStats") or {}
    session = raw.get("session") or {}
    elapsed_ms = parse_duration_ms(stats.get("elapsedTime"))
    query_text = raw.get("query") or ""
    preview, truncated = truncate_utf8(str(query_text), query_text_max_bytes)

    resource_group = raw.get("resourceGroupId")
    if not isinstance(resource_group, list):
        resource_group = None

    return {
        "query_id": raw.get("queryId"),
        "state": raw.get("state"),
        "user": session.get("user"),
        "source": session.get("source"),
        "resource_group_id": resource_group,
        "elapsed_ms": elapsed_ms,
        "queued_ms": parse_duration_ms(stats.get("queuedTime")),
        "total_cpu_ms": parse_duration_ms(stats.get("totalCpuTime")),
        "peak_user_memory_bytes": parse_data_size_bytes(
            stats.get("peakUserMemoryReservation")
        ),
        "physical_input_bytes": parse_data_size_bytes(stats.get("physicalInputDataSize")),
        "progress_percentage": stats.get("progressPercentage"),
        "running_drivers": stats.get("runningDrivers"),
        "queued_drivers": stats.get("queuedDrivers"),
        "fully_blocked": stats.get("fullyBlocked"),
        "query_preview": preview,
        "query_truncated": truncated,
        "long_running": bool(
            elapsed_ms is not None and elapsed_ms >= long_running_seconds * 1000.0
        ),
    }


class ClusterPoller:
    """Polls one coordinator on three independent schedules."""

    def __init__(
        self,
        cluster_name: str,
        client: TrinoClient,
        repository: SnapshotRepository,
        query_interval: float = 5.0,
        jmx_interval: float = 15.0,
        info_interval: float = 30.0,
        query_text_max_bytes: int = 4096,
        long_running_seconds: float = 300.0,
        response_backoff_bytes: int = 5_000_000,
        response_backoff_interval: float = 10.0,
        clock: Optional[Callable[[], float]] = None,
        jmx_cross_check_max_age: Optional[float] = None,
        wall_clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.cluster_name = cluster_name
        self.client = client
        self.repository = repository
        self.base_query_interval = query_interval
        self.jmx_interval = jmx_interval
        self.info_interval = info_interval
        self.query_text_max_bytes = query_text_max_bytes
        self.long_running_seconds = long_running_seconds
        self.response_backoff_bytes = response_backoff_bytes
        self.response_backoff_interval = response_backoff_interval
        # A JMX reading older than a few poll intervals says nothing about the
        # query list we just fetched, so it must not be used to judge it.
        self.jmx_cross_check_max_age = (
            jmx_cross_check_max_age
            if jmx_cross_check_max_age is not None
            else jmx_interval * 3.0
        )
        self._wall_clock = wall_clock or utcnow

        if clock is None:
            import time

            clock = time.monotonic
        self._clock = clock

        self.query_interval = query_interval
        self._next_due = {KIND_QUERIES: 0.0, KIND_JMX: 0.0, KIND_INFO: 0.0}
        # Last successful collection per kind - exported so sre-agent can alert
        # on "TMS went blind" rather than on the absence of data.
        self.last_success: Dict[str, Optional[float]] = {
            KIND_QUERIES: None,
            KIND_JMX: None,
            KIND_INFO: None,
        }

    # ----------------------------------------------------------- scheduling

    def due_kinds(self) -> List[str]:
        now = self._clock()
        return [kind for kind, due_at in self._next_due.items() if now >= due_at]

    def _reschedule(self, kind: str) -> None:
        interval = {
            KIND_QUERIES: self.query_interval,
            KIND_JMX: self.jmx_interval,
            KIND_INFO: self.info_interval,
        }[kind]
        self._next_due[kind] = self._clock() + interval

    def seconds_until_next_due(self) -> float:
        now = self._clock()
        return max(0.0, min(self._next_due.values()) - now)

    # --------------------------------------------------------------- polls

    def _failed_snapshot(self, kind: str, error: TrinoClientError) -> Snapshot:
        return Snapshot(
            cluster=self.cluster_name,
            kind=kind,
            collected_at=utcnow(),
            payload={},
            collection_error="{}: {}".format(type(error).__name__, error),
            advice=error.advice or None,
        )

    def poll_info(self) -> Snapshot:
        """GET /v1/info - PUBLIC, so this keeps answering when authorisation is
        broken. It is the last signal TMS has that a coordinator is alive."""
        try:
            info = self.client.get_server_info()
        except TrinoClientError as exc:
            log.warning("info poll failed for %s: %s", self.cluster_name, exc)
            return self._failed_snapshot(KIND_INFO, exc)
        self.last_success[KIND_INFO] = self._clock()
        return Snapshot(
            cluster=self.cluster_name,
            kind=KIND_INFO,
            collected_at=utcnow(),
            payload={"info": info},
        )

    def poll_jmx(self) -> Snapshot:
        """Read the health MBeans.

        A single missing MBean does not fail the whole poll: the others still
        feed their health tests, and the failure is recorded per MBean so the
        operator sees which one broke.
        """
        mbeans: Dict[str, Any] = {}
        errors: Dict[str, str] = {}
        advice: Optional[str] = None
        for object_name in JMX_MBEANS:
            try:
                mbeans[object_name] = self.client.get_mbean(object_name)
            except TrinoClientError as exc:
                errors[object_name] = "{}: {}".format(type(exc).__name__, exc)
                if advice is None and exc.advice:
                    advice = exc.advice
                log.warning(
                    "jmx poll failed for %s %s: %s", self.cluster_name, object_name, exc
                )

        if not mbeans:
            return Snapshot(
                cluster=self.cluster_name,
                kind=KIND_JMX,
                collected_at=utcnow(),
                payload={"errors": errors},
                collection_error="all MBean reads failed",
                advice=advice,
            )

        self.last_success[KIND_JMX] = self._clock()
        return Snapshot(
            cluster=self.cluster_name,
            kind=KIND_JMX,
            collected_at=utcnow(),
            payload={"mbeans": mbeans, "errors": errors},
            # Partial failure is still usable, but the operator must be told.
            collection_error=(
                "{} of {} MBeans unavailable".format(len(errors), len(JMX_MBEANS))
                if errors
                else None
            ),
            advice=advice,
        )

    def poll_queries(self, jmx_running_queries: Optional[int] = None) -> Snapshot:
        """GET /v1/query plus the H-09 cross-check.

        `jmx_running_queries` comes from the most recent JMX snapshot. When the
        list is empty but JMX says queries are running, the empty list is a
        permission denial wearing the costume of an idle cluster.
        """
        try:
            result = self.client.list_queries()
        except TrinoClientError as exc:
            log.warning("query poll failed for %s: %s", self.cluster_name, exc)
            return self._failed_snapshot(KIND_QUERIES, exc)

        queries = [
            summarise_query(raw, self.query_text_max_bytes, self.long_running_seconds)
            for raw in result.queries
        ]
        summary = {
            "running": sum(1 for q in queries if q["state"] in _RUNNING_STATES),
            "queued": sum(1 for q in queries if q["state"] in _QUEUED_STATES),
            "long_running": sum(1 for q in queries if q["long_running"]),
            "total": len(queries),
        }

        collection_error = None
        advice = None
        if not queries and jmx_running_queries:
            # H-09. Do not let this pass as an idle cluster.
            collection_error = (
                "query list is empty but JMX reports {} running queries - "
                "the list is most likely being filtered by access control".format(
                    jmx_running_queries
                )
            )
            advice = (
                "Check the tms-svc account's queries: view grant in rules.json. "
                "With file access control a denied list arrives as an empty list, not a 403."
            )
            log.error("H-09 tripped for %s: %s", self.cluster_name, collection_error)

        self._adapt_interval(result.response_bytes)
        if collection_error is None:
            self.last_success[KIND_QUERIES] = self._clock()

        return Snapshot(
            cluster=self.cluster_name,
            kind=KIND_QUERIES,
            collected_at=utcnow(),
            payload={
                "queries": queries,
                "summary": summary,
                "response_bytes": result.response_bytes,
                "poll_interval_seconds": self.query_interval,
            },
            collection_error=collection_error,
            advice=advice,
        )

    def _adapt_interval(self, response_bytes: int) -> None:
        """Back off when responses get large, recover when they shrink.

        Measured 2026-08-06 at ~3.5 KB per running query, but peak concurrency
        is unknown, so the interval adapts instead of relying on that figure.
        """
        if response_bytes >= self.response_backoff_bytes:
            if self.query_interval < self.response_backoff_interval:
                log.warning(
                    "%s: /v1/query returned %d bytes, raising poll interval %.1fs -> %.1fs",
                    self.cluster_name,
                    response_bytes,
                    self.query_interval,
                    self.response_backoff_interval,
                )
            self.query_interval = self.response_backoff_interval
        elif response_bytes < self.response_backoff_bytes // 2:
            # Hysteresis: only recover well below the trigger, so a response
            # hovering at the threshold does not oscillate the schedule.
            self.query_interval = self.base_query_interval

    # ----------------------------------------------------------------- tick

    def tick(self) -> List[Snapshot]:
        """Run whichever polls are due and persist their snapshots."""
        produced: List[Snapshot] = []
        for kind in self.due_kinds():
            if kind == KIND_INFO:
                snapshot = self.poll_info()
            elif kind == KIND_JMX:
                snapshot = self.poll_jmx()
            else:
                snapshot = self.poll_queries(self._last_jmx_running_queries())
            self._reschedule(kind)
            try:
                self.repository.save(snapshot)
            except Exception:  # noqa: BLE001 - storage must not kill the loop
                log.exception("failed to persist %s snapshot for %s", kind, self.cluster_name)
            produced.append(snapshot)
        return produced

    def _last_jmx_running_queries(self) -> Optional[int]:
        snapshot = self.repository.load(self.cluster_name, KIND_JMX)
        if snapshot is None:
            return None
        if snapshot.is_stale(self._wall_clock(), self.jmx_cross_check_max_age):
            # Queries are polled before JMX on every tick, so on the first tick
            # after a restart the stored JMX snapshot can be arbitrarily old.
            # Comparing a fresh empty list against yesterday's RunningQueries
            # raises H-09 on a cluster that is merely idle now, which discredits
            # the one alarm that catches genuinely silent filtering.
            log.debug(
                "%s: JMX snapshot too old for the H-09 cross-check, skipping",
                self.cluster_name,
            )
            return None
        value = _get(
            snapshot.payload, "mbeans", "trino.execution:name=QueryManager", "RunningQueries"
        )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)
