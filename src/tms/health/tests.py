"""The health test catalogue, H-01 to H-09.

Implements docs/HEALTH_TESTS.md. Every test answers the same question from a
different angle: can this cluster actually run queries? A live process is not
the same thing, which is the whole point of the synthetic health model.

Two rules hold for every test in here:

* BAD and CONCERNING must carry `advice`. An alert with no remedy wastes the
  responder's time (TEAMS.md sre-agent rule 1), and both the engine and a
  database CHECK constraint reject a transition without one.
* A missing input yields UNKNOWN, never GOOD. Absence of evidence is not
  evidence of health.

Python 3.9 compatible.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from tms.health.states import BAD, CONCERNING, GOOD, UNKNOWN

log = logging.getLogger(__name__)

QUERY_MANAGER_MBEAN = "trino.execution:name=QueryManager"
MEMORY_MBEAN = "java.lang:type=Memory"
CLUSTER_MEMORY_MBEAN = "trino.memory:name=ClusterMemoryManager"
NODE_MANAGER_MBEAN = "trino.node:name=CoordinatorNodeManager"


class HealthResult:
    __slots__ = ("test_id", "name", "state", "observed_value", "threshold", "advice")

    def __init__(
        self,
        test_id: str,
        name: str,
        state: str,
        observed_value: Any = None,
        threshold: Any = None,
        advice: str = "",
    ) -> None:
        self.test_id = test_id
        self.name = name
        self.state = state
        self.observed_value = observed_value
        self.threshold = threshold
        self.advice = advice

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.test_id,
            "name": self.name,
            "state": self.state,
            "observed_value": self.observed_value,
            "threshold": self.threshold,
            "advice": self.advice,
        }

    def __repr__(self) -> str:
        return "HealthResult({}, {})".format(self.test_id, self.state)


class HealthContext:
    """Everything a test may read. Snapshots may be None or untrusted."""

    def __init__(
        self,
        cluster_name: str,
        expected_workers: int,
        thresholds: Dict[str, float],
        info: Optional[Any] = None,
        jmx: Optional[Any] = None,
        queries: Optional[Any] = None,
        coordinator_counted_in_active_nodes: bool = True,
        gateway_enabled: bool = False,
        gateway_backends: Optional[List[Dict[str, Any]]] = None,
        previous_oom_kills: Optional[int] = None,
    ) -> None:
        self.cluster_name = cluster_name
        self.expected_workers = expected_workers
        self.thresholds = thresholds
        self.info = info
        self.jmx = jmx
        self.queries = queries
        self.coordinator_counted_in_active_nodes = coordinator_counted_in_active_nodes
        self.gateway_enabled = gateway_enabled
        self.gateway_backends = gateway_backends
        self.previous_oom_kills = previous_oom_kills
        # Written by H-07 so the engine can carry the counter forward.
        self.current_oom_kills: Optional[int] = None

    def threshold(self, key: str, default: float) -> float:
        value = self.thresholds.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def mbean(self, object_name: str) -> Optional[Dict[str, Any]]:
        if self.jmx is None or not isinstance(self.jmx.payload, dict):
            return None
        mbeans = self.jmx.payload.get("mbeans")
        if not isinstance(mbeans, dict):
            return None
        value = mbeans.get(object_name)
        return value if isinstance(value, dict) else None

    def attribute(self, object_name: str, attribute: str) -> Optional[Any]:
        mbean = self.mbean(object_name)
        if mbean is None:
            return None
        return mbean.get(attribute)

    def number(self, object_name: str, attribute: str) -> Optional[float]:
        value = self.attribute(object_name, attribute)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)


def _unknown(test_id: str, name: str, reason: str) -> HealthResult:
    return HealthResult(test_id, name, UNKNOWN, advice=reason)


def _jmx_unavailable(test_id: str, name: str, ctx: HealthContext) -> HealthResult:
    """Shared UNKNOWN for tests whose MBean could not be read.

    Surfaces the collector's advice verbatim so a permission problem reads as a
    permission problem rather than as a cluster fault.
    """
    advice = None
    if ctx.jmx is not None:
        advice = getattr(ctx.jmx, "advice", None)
    return _unknown(
        test_id,
        name,
        advice
        or "Could not read the JMX metric. Check that tms-svc has system_information: read in rules.json, and that the coordinator is reachable.",
    )


# --------------------------------------------------------------------- H-01

def h01_coordinator_responsive(ctx: HealthContext) -> HealthResult:
    """GET /v1/info is PUBLIC, so this keeps answering when authorisation is
    broken. It is the last signal that a coordinator is alive."""
    name = "Coordinator responsiveness"
    if ctx.info is None:
        return _unknown("H-01", name, "No data collected yet.")
    if not ctx.info.trustworthy:
        return HealthResult(
            "H-01",
            name,
            BAD,
            observed_value="unreachable",
            advice=getattr(ctx.info, "advice", None)
            or "The coordinator is not responding. Check its systemd unit and logs — this cluster cannot accept new queries.",
        )
    return HealthResult("H-01", name, GOOD, observed_value="responsive")


# --------------------------------------------------------------------- H-02

def h02_startup_complete(ctx: HealthContext) -> HealthResult:
    """`starting` is a boolean field on the ServerInfo record (verified @477)."""
    name = "Startup complete"
    if ctx.info is None or not ctx.info.trustworthy:
        return _unknown("H-02", name, "Could not read coordinator info.")
    info = (ctx.info.payload or {}).get("info")
    if not isinstance(info, dict) or "starting" not in info:
        return _unknown("H-02", name, "The /v1/info response has no `starting` field.")
    if bool(info.get("starting")):
        return HealthResult(
            "H-02",
            name,
            CONCERNING,
            observed_value="starting",
            advice="The coordinator is still starting — catalogs may still be loading. If this persists for more than a few minutes, suspect a catalog configuration error.",
        )
    return HealthResult("H-02", name, GOOD, observed_value="started")


# --------------------------------------------------------------------- H-03

def h03_worker_registration(ctx: HealthContext) -> HealthResult:
    """Unplanned missing workers only.

    Draining and shutting-down nodes are deliberate. Painting the health page
    red while an operator shrinks the fleet on purpose is how a console loses
    its credibility - after that, nobody reads it during a real incident.
    """
    name = "Worker registration"
    active = ctx.number(NODE_MANAGER_MBEAN, "ActiveNodeCount")
    if active is None:
        return _jmx_unavailable("H-03", name, ctx)

    active_workers = int(active)
    if ctx.coordinator_counted_in_active_nodes:
        # Verified 2026-08-06: a 12-worker cluster reports 13.
        active_workers -= 1

    planned = 0
    for attribute in ("DrainingNodeCount", "DrainedNodeCount", "ShuttingDownNodeCount"):
        value = ctx.number(NODE_MANAGER_MBEAN, attribute)
        if value is not None:
            planned += int(value)

    expected = ctx.expected_workers
    unplanned = max(0, expected - active_workers - planned)
    bad_pct = ctx.threshold("missing_workers_pct_bad", 20.0)
    bad_threshold = max(1, int(expected * bad_pct / 100.0))

    observed = {
        "active_workers": active_workers,
        "expected_workers": expected,
        "planned_out": planned,
        "unplanned_missing": unplanned,
    }

    if unplanned == 0:
        return HealthResult("H-03", name, GOOD, observed, expected)

    planned_note = (
        " {} of those are in a planned shutdown.".format(planned) if planned else ""
    )
    capacity_pct = int(100 * active_workers / expected) if expected else 0
    advice = (
        "Only {active} of {expected} workers are active ({unplanned} missing outside "
        "any planned drain). Check the systemd unit and discovery config on the "
        "workers that have not joined. Cluster capacity is at {pct}%.{planned}"
    ).format(
        expected=expected,
        active=active_workers,
        unplanned=unplanned,
        pct=capacity_pct,
        planned=planned_note,
    )
    state = BAD if unplanned >= bad_threshold else CONCERNING
    return HealthResult("H-03", name, state, observed, expected, advice)


# --------------------------------------------------------------------- H-04

def h04_heap_usage(ctx: HealthContext) -> HealthResult:
    name = "Coordinator heap"
    usage = ctx.attribute(MEMORY_MBEAN, "HeapMemoryUsage")
    if not isinstance(usage, dict):
        return _jmx_unavailable("H-04", name, ctx)
    used = usage.get("used")
    maximum = usage.get("max")
    if not isinstance(used, (int, float)) or not isinstance(maximum, (int, float)):
        return _unknown("H-04", name, "Could not read used/max from HeapMemoryUsage.")
    if maximum <= 0:
        return _unknown("H-04", name, "The JVM reports no heap maximum (-1). Check jvm.config.")

    pct = 100.0 * used / maximum
    concerning = ctx.threshold("heap_used_pct_concerning", 80.0)
    bad = ctx.threshold("heap_used_pct_bad", 90.0)
    if pct >= bad:
        state = BAD
    elif pct >= concerning:
        state = CONCERNING
    else:
        state = GOOD

    advice = ""
    if state != GOOD:
        advice = (
            "Coordinator heap is at {:.0f}%. GC pressure slows every query. Check "
            "concurrent query count; if this persists, consider a resource-group "
            "concurrency limit or more heap."
        ).format(pct)
    return HealthResult("H-04", name, state, round(pct, 1), bad, advice)


# --------------------------------------------------------------------- H-05

def h05_query_failure_rate(ctx: HealthContext) -> HealthResult:
    """Failures as a share of queries that reached a terminal state.

    The denominator is CompletedQueries, not StartedQueries. Measured on 477
    (TRINO_VERIFIED.md T3-7): a query rejected during analysis increments
    Submitted, Completed and Failed but never Started, so failed/started
    exceeds 100% on any cluster where queries fail before execution begins -
    which is exactly what a bad catalog or a permission problem produces. A
    health page that reports "120% of queries failed" teaches operators to
    stop believing it.
    """
    name = "Query failure rate (5m)"
    failed = ctx.number(QUERY_MANAGER_MBEAN, "FailedQueries.FiveMinute.Count")
    completed = ctx.number(QUERY_MANAGER_MBEAN, "CompletedQueries.FiveMinute.Count")
    if failed is None or completed is None:
        return _jmx_unavailable("H-05", name, ctx)
    if completed <= 0:
        # No traffic is not health. It may itself be the incident.
        return _unknown(
            "H-05",
            name,
            "No queries completed in the last 5 minutes, so there is no failure "
            "rate to compute. Traffic stopping is itself worth investigating.",
        )

    pct = 100.0 * failed / completed
    if pct > 100.0:
        # Cannot happen once the denominator is right, but a health page must
        # never render an impossible number, so clamp loudly instead.
        log.warning(
            "H-05: failed (%s) exceeds completed (%s) - clamping to 100%%",
            failed,
            completed,
        )
        pct = 100.0
    concerning = ctx.threshold("failure_rate_pct_concerning", 5.0)
    bad = ctx.threshold("failure_rate_pct_bad", 20.0)
    if pct >= bad:
        state = BAD
    elif pct >= concerning:
        state = CONCERNING
    else:
        state = GOOD

    advice = ""
    if state != GOOD:
        advice = (
            "{:.1f}% of queries failed in the last 5 minutes. Read this with H-06 "
            "(internal failures) to tell user SQL errors from engine problems."
        ).format(pct)
    return HealthResult("H-05", name, state, round(pct, 1), bad, advice)


# --------------------------------------------------------------------- H-06

def h06_internal_failures(ctx: HealthContext) -> HealthResult:
    """Separated from H-05 because the layers differ.

    Any number of user syntax errors leaves the cluster healthy. A single
    internal failure does not.
    """
    name = "Internal failures (5m)"
    internal = ctx.number(QUERY_MANAGER_MBEAN, "InternalFailures.FiveMinute.Count")
    if internal is None:
        return _jmx_unavailable("H-06", name, ctx)

    count = int(internal)
    concerning = ctx.threshold("internal_failures_concerning", 1.0)
    bad = ctx.threshold("internal_failures_bad", 5.0)
    if count >= bad:
        state = BAD
    elif count >= concerning:
        state = CONCERNING
    else:
        state = GOOD

    advice = ""
    if state != GOOD:
        advice = (
            "{} internal errors in the last 5 minutes. These are engine or "
            "infrastructure faults, not user SQL. Check coordinator and worker logs."
        ).format(count)
    return HealthResult("H-06", name, state, count, bad, advice)


# --------------------------------------------------------------------- H-07

def h07_oom_kills(ctx: HealthContext) -> HealthResult:
    """Cumulative counter - judged on the delta.

    Reading the absolute value would leave the cluster permanently BAD after the
    first OOM until the next coordinator restart.
    """
    name = "Out-of-memory kills"
    total = ctx.number(CLUSTER_MEMORY_MBEAN, "QueriesKilledDueToOutOfMemory")
    if total is None:
        return _jmx_unavailable("H-07", name, ctx)

    current = int(total)
    ctx.current_oom_kills = current
    if ctx.previous_oom_kills is None:
        return HealthResult("H-07", name, GOOD, {"total": current, "delta": None})

    # A restart resets the counter; treat a decrease as no new kills.
    delta = max(0, current - ctx.previous_oom_kills)
    concerning = ctx.threshold("oom_kills_concerning", 1.0)
    bad = ctx.threshold("oom_kills_bad", 3.0)
    if delta >= bad:
        state = BAD
    elif delta >= concerning:
        state = CONCERNING
    else:
        state = GOOD

    advice = ""
    if state != GOOD:
        advice = (
            "{} queries were killed for running out of memory. Something large is "
            "squeezing the cluster — check the top memory consumers in Live Queries."
        ).format(delta)
    return HealthResult(
        "H-07", name, state, {"total": current, "delta": delta}, bad, advice
    )


# --------------------------------------------------------------------- H-08

def h08_gateway_registration(ctx: HealthContext) -> HealthResult:
    """Optional. Removed from the catalogue entirely when the Gateway adapter is
    disabled - a test that is permanently UNKNOWN is noise, not information."""
    name = "Gateway backend registration"
    if ctx.gateway_backends is None:
        return _unknown("H-08", name, "Could not read Gateway backend list.")

    # ⛔ Match on the joined `cluster`, not on the Gateway's own backend name.
    # The two routinely differ - a Gateway backend called `trino-prod-a-1`
    # fronts the TMS cluster `prod-a` - and the collector already resolves that
    # by comparing coordinator URLs. Comparing names would report a correctly
    # registered cluster as "not registered", which reads as routing being
    # broken when nothing is wrong.
    entry = None
    for backend in ctx.gateway_backends:
        if not isinstance(backend, dict):
            continue
        if backend.get("cluster") == ctx.cluster_name:
            entry = backend
            break
        # Fall back to the name only when no join was recorded at all, so a
        # payload from an older collector still works.
        if "cluster" not in backend and backend.get("name") == ctx.cluster_name:
            entry = backend
            break

    if entry is None:
        return HealthResult(
            "H-08",
            name,
            BAD,
            observed_value="not registered",
            advice="This cluster is not in the Gateway backend list, so no new "
            "queries will be routed to it. Check the Gateway configuration.",
        )
    if not entry.get("active"):
        return HealthResult(
            "H-08",
            name,
            CONCERNING,
            observed_value="inactive",
            advice="This cluster is deactivated in the Gateway. Ignore this if it "
            "is deliberate (maintenance); otherwise no new queries reach it.",
        )
    return HealthResult("H-08", name, GOOD, observed_value="active")


# --------------------------------------------------------------------- H-09

def h09_permission_self_check(ctx: HealthContext) -> HealthResult:
    """Detects the silent failure mode of `file` access control.

    A denied `queries` rule filters the list to empty instead of returning 403,
    so a forbidden cluster and an idle one produce identical responses. The
    collector cross-checks against JMX RunningQueries and records the mismatch;
    this test surfaces it rather than letting TMS report "0 running queries" on
    a busy cluster.
    """
    name = "Query-list permission"
    if ctx.queries is None:
        return _unknown("H-09", name, "No query snapshot collected yet.")
    if ctx.queries.trustworthy:
        return HealthResult("H-09", name, GOOD, observed_value="consistent")
    return HealthResult(
        "H-09",
        name,
        UNKNOWN,
        observed_value=ctx.queries.collection_error,
        advice=getattr(ctx.queries, "advice", None)
        or "The query list cannot be trusted. Check the tms-svc account's queries "
        "grant in rules.json.",
    )


# ---------------------------------------------------------------- catalogue

ALL_TESTS: Dict[str, Callable[[HealthContext], HealthResult]] = {
    "H-01": h01_coordinator_responsive,
    "H-02": h02_startup_complete,
    "H-03": h03_worker_registration,
    "H-04": h04_heap_usage,
    "H-05": h05_query_failure_rate,
    "H-06": h06_internal_failures,
    "H-07": h07_oom_kills,
    "H-08": h08_gateway_registration,
    "H-09": h09_permission_self_check,
}

# Only meaningful when the Gateway adapter is on (B6 still open).
GATEWAY_TESTS = frozenset(["H-08"])
