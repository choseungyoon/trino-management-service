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

from typing import Any, Callable, Dict, List, Optional

from tms.health.states import BAD, CONCERNING, GOOD, UNKNOWN

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
        or "JMX 지표를 읽지 못했다. tms-svc 의 system_information:read 권한과 "
        "코디네이터 상태를 확인하라.",
    )


# --------------------------------------------------------------------- H-01

def h01_coordinator_responsive(ctx: HealthContext) -> HealthResult:
    """GET /v1/info is PUBLIC, so this keeps answering when authorisation is
    broken. It is the last signal that a coordinator is alive."""
    name = "코디네이터 응답성"
    if ctx.info is None:
        return _unknown("H-01", name, "아직 수집된 데이터가 없다.")
    if not ctx.info.trustworthy:
        return HealthResult(
            "H-01",
            name,
            BAD,
            observed_value="unreachable",
            advice=getattr(ctx.info, "advice", None)
            or "코디네이터가 응답하지 않는다. systemd 유닛 상태와 코디네이터 로그를 "
            "확인하라. 이 클러스터는 신규 쿼리를 받지 못한다.",
        )
    return HealthResult("H-01", name, GOOD, observed_value="responsive")


# --------------------------------------------------------------------- H-02

def h02_startup_complete(ctx: HealthContext) -> HealthResult:
    """`starting` is a boolean field on the ServerInfo record (verified @477)."""
    name = "기동 완료"
    if ctx.info is None or not ctx.info.trustworthy:
        return _unknown("H-02", name, "코디네이터 정보를 읽지 못했다.")
    info = (ctx.info.payload or {}).get("info")
    if not isinstance(info, dict) or "starting" not in info:
        return _unknown("H-02", name, "/v1/info 응답에 starting 필드가 없다.")
    if bool(info.get("starting")):
        return HealthResult(
            "H-02",
            name,
            CONCERNING,
            observed_value="starting",
            advice="코디네이터가 아직 기동 중이다. 카탈로그 로딩이 끝나지 않았을 수 "
            "있다. 수 분 후에도 지속되면 카탈로그 설정 오류를 의심하라.",
        )
    return HealthResult("H-02", name, GOOD, observed_value="started")


# --------------------------------------------------------------------- H-03

def h03_worker_registration(ctx: HealthContext) -> HealthResult:
    """Unplanned missing workers only.

    Draining and shutting-down nodes are deliberate. Painting the health page
    red while an operator shrinks the fleet on purpose is how a console loses
    its credibility - after that, nobody reads it during a real incident.
    """
    name = "워커 등록 수"
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
        " 이 중 {}대는 계획된 종료 절차 중이다.".format(planned) if planned else ""
    )
    capacity_pct = int(100 * active_workers / expected) if expected else 0
    advice = (
        "워커 {expected}대 중 {active}대만 활성이다(예정 외 {unplanned}대 누락). "
        "미조인 워커의 systemd 상태와 discovery 설정을 확인하라. "
        "클러스터 용량이 {pct}%로 떨어져 있다.{planned}"
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
    name = "코디네이터 힙 사용률"
    usage = ctx.attribute(MEMORY_MBEAN, "HeapMemoryUsage")
    if not isinstance(usage, dict):
        return _jmx_unavailable("H-04", name, ctx)
    used = usage.get("used")
    maximum = usage.get("max")
    if not isinstance(used, (int, float)) or not isinstance(maximum, (int, float)):
        return _unknown("H-04", name, "HeapMemoryUsage 에서 used/max 를 읽지 못했다.")
    if maximum <= 0:
        return _unknown("H-04", name, "힙 최대값이 보고되지 않는다(-1). JVM 설정을 확인하라.")

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
            "코디네이터 힙이 {:.0f}%다. GC 부하로 전체 쿼리가 느려진다. 동시 실행 "
            "쿼리 수를 확인하고, 지속되면 리소스 그룹 동시성 제한 또는 힙 증설을 "
            "검토하라."
        ).format(pct)
    return HealthResult("H-04", name, state, round(pct, 1), bad, advice)


# --------------------------------------------------------------------- H-05

def h05_query_failure_rate(ctx: HealthContext) -> HealthResult:
    name = "쿼리 실패율 (5분)"
    failed = ctx.number(QUERY_MANAGER_MBEAN, "FailedQueries.FiveMinute.Count")
    started = ctx.number(QUERY_MANAGER_MBEAN, "StartedQueries.FiveMinute.Count")
    if failed is None or started is None:
        return _jmx_unavailable("H-05", name, ctx)
    if started <= 0:
        # No traffic is not health. It may itself be the incident.
        return _unknown(
            "H-05",
            name,
            "최근 5분간 시작된 쿼리가 없어 실패율을 계산할 수 없다. "
            "유입이 끊긴 것 자체가 이상 신호일 수 있다.",
        )

    pct = 100.0 * failed / started
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
            "최근 5분 쿼리 실패율 {:.1f}%. 사용자 오류(문법)와 시스템 오류를 "
            "구분하려면 H-06(내부 실패)을 함께 보라."
        ).format(pct)
    return HealthResult("H-05", name, state, round(pct, 1), bad, advice)


# --------------------------------------------------------------------- H-06

def h06_internal_failures(ctx: HealthContext) -> HealthResult:
    """Separated from H-05 because the layers differ.

    Any number of user syntax errors leaves the cluster healthy. A single
    internal failure does not.
    """
    name = "내부(시스템) 실패 (5분)"
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
            "최근 5분간 내부 오류 {}건. 사용자 SQL 문제가 아니라 엔진/인프라 "
            "문제다. 코디네이터·워커 로그를 확인하라."
        ).format(count)
    return HealthResult("H-06", name, state, count, bad, advice)


# --------------------------------------------------------------------- H-07

def h07_oom_kills(ctx: HealthContext) -> HealthResult:
    """Cumulative counter - judged on the delta.

    Reading the absolute value would leave the cluster permanently BAD after the
    first OOM until the next coordinator restart.
    """
    name = "메모리 부족 강제 종료"
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
            "메모리 부족으로 쿼리 {}건이 강제 종료됐다. 대용량 쿼리가 클러스터를 "
            "압박하고 있다. 실행 중 쿼리 화면에서 메모리 상위 쿼리를 확인하라."
        ).format(delta)
    return HealthResult(
        "H-07", name, state, {"total": current, "delta": delta}, bad, advice
    )


# --------------------------------------------------------------------- H-08

def h08_gateway_registration(ctx: HealthContext) -> HealthResult:
    """Optional. Removed from the catalogue entirely when the Gateway adapter is
    disabled - a test that is permanently UNKNOWN is noise, not information."""
    name = "Gateway 백엔드 등록 상태"
    if ctx.gateway_backends is None:
        return _unknown("H-08", name, "Gateway 정보를 읽지 못했다.")

    entry = None
    for backend in ctx.gateway_backends:
        if isinstance(backend, dict) and backend.get("name") == ctx.cluster_name:
            entry = backend
            break

    if entry is None:
        return HealthResult(
            "H-08",
            name,
            BAD,
            observed_value="not registered",
            advice="이 클러스터가 Gateway 백엔드 목록에 없다. 신규 쿼리가 이 "
            "클러스터로 오지 않는다. Gateway 설정을 확인하라.",
        )
    if not entry.get("active"):
        return HealthResult(
            "H-08",
            name,
            CONCERNING,
            observed_value="inactive",
            advice="이 클러스터가 Gateway 에서 비활성 상태다. 의도한 것이라면"
            "(작업 중) 무시하라. 아니라면 신규 쿼리가 이 클러스터로 오지 않는다.",
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
    name = "쿼리 조회 권한 자가진단"
    if ctx.queries is None:
        return _unknown("H-09", name, "아직 수집된 쿼리 스냅샷이 없다.")
    if ctx.queries.trustworthy:
        return HealthResult("H-09", name, GOOD, observed_value="consistent")
    return HealthResult(
        "H-09",
        name,
        UNKNOWN,
        observed_value=ctx.queries.collection_error,
        advice=getattr(ctx.queries, "advice", None)
        or "쿼리 목록을 신뢰할 수 없다. rules.json 에서 tms-svc 의 queries 권한을 "
        "확인하라.",
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
