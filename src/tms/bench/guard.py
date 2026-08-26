"""Was a cluster out of rotation and idle when a benchmark ran?

A label, not a gate: benchmarks run against live clusters on purpose. The
answer is stored on the run (`benchmark_run.guard`) and shown next to the
numbers, because a result taken on a quiet cluster and one taken under load
are not two measurements of the same thing.

⛔ Routing state is read from the Gateway live, not from the snapshot. A
snapshot one poll old can call a backend deactivated twenty seconds after it
was re-activated. The backend->cluster mapping still comes from the snapshot;
only "is it active" is asked live.

⛔ This module never deactivates anything. Stopping intake belongs to the safe
restart sequence, which drains queries first.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: Why a cluster is not exclusive. The screen renders these; they are not free
#: text so that a test can assert on the reason rather than on a sentence.
NO_GATEWAY = "no_gateway"
NO_BACKEND = "no_backend"
STILL_ROUTED = "still_routed"
GATEWAY_UNREACHABLE = "gateway_unreachable"
QUERIES_RUNNING = "queries_running"
NO_QUERY_VIEW = "no_query_view"
STALE_QUERY_VIEW = "stale_query_view"

ADVICE = {
    NO_GATEWAY: (
        "The Gateway integration is off, so there is no way to tell whether "
        "this cluster is taking production traffic. Runs will be recorded as "
        "though it were."),
    NO_BACKEND: (
        "No Gateway backend is matched to this cluster, so there is no way "
        "to tell whether it is in rotation. Runs will be recorded as though "
        "it were."),
    GATEWAY_UNREACHABLE: (
        "The Gateway did not answer, so its routing state is unknown. An "
        "unknown routing state is recorded as a serving one."),
    # Conditions, not instructions: nothing here has to be fixed before
    # running. They describe what the numbers will contain.
    STILL_ROUTED: (
        "This cluster is in rotation, so production queries are landing on it "
        "while the benchmark runs. To measure it quiet instead, exclude it in "
        "the Gateway first — this console will not deactivate a backend on "
        "its own."),
    NO_QUERY_VIEW: (
        "This cluster's running queries have not been collected yet, so "
        "there is no way to tell whether the coordinator is idle."),
    STALE_QUERY_VIEW: (
        "The running-query view is stale. What it shows is the past, and the "
        "past does not say the cluster is idle now."),
    QUERIES_RUNNING: (
        "Queries are running here. They compete with the benchmark for the "
        "same workers, so the numbers measure them as much as the cluster."),
}


class GuardResult:
    """What the cluster looked like when the run started.

    ⛔ `ok` and `refusals` are serialised into `benchmark_run.guard`. Renaming
    them would make every row written before they changed meaning read as
    though the cluster had been live. `ok` means "was quiet", not "may run".
    """

    __slots__ = ("cluster", "refusals", "backends", "running_queries",
                 "observed_at", "checked_gateway_live")

    def __init__(self, cluster: str) -> None:
        self.cluster = cluster
        self.refusals: List[str] = []
        self.backends: List[Dict[str, Any]] = []
        self.running_queries: Optional[int] = None
        self.observed_at = None
        self.checked_gateway_live = False

    @property
    def ok(self) -> bool:
        return not self.refusals

    def refuse(self, code: str) -> None:
        if code not in self.refusals:
            self.refusals.append(code)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cluster": self.cluster,
            "ok": self.ok,
            "refusals": list(self.refusals),
            "advice": [{"code": c, "text": ADVICE.get(c, c)} for c in self.refusals],
            "backends": list(self.backends),
            "running_queries": self.running_queries,
            "observed_at": (self.observed_at.isoformat()
                            if hasattr(self.observed_at, "isoformat")
                            else self.observed_at),
            "checked_gateway_live": self.checked_gateway_live,
        }

    def summary(self) -> str:
        if self.ok:
            return "Excluded from routing and idle."
        return " ".join(ADVICE.get(code, code) for code in self.refusals)

    def caveat(self) -> str:
        """One line for a run taken on a cluster that was not exclusive."""
        if self.ok:
            return ""
        return ("These numbers include whatever else the cluster was doing. "
                + self.summary())


def check(cluster: str, gateway_client, snapshots, stale_threshold: float,
          queries_envelope: Optional[Dict[str, Any]] = None) -> GuardResult:
    """Was `cluster` exclusive - out of rotation and idle - right now?

    Answers the question; it does not decide anything. The caller records the
    answer on the run and shows it next to the numbers.

    `queries_envelope` is the already-loaded running-query view when the caller
    has one (the screen does); omitted, it is read from the snapshot store.
    """
    from tms.collector.snapshot import GATEWAY_SCOPE, KIND_GATEWAY, KIND_QUERIES

    result = GuardResult(cluster)

    # ── is it out of rotation? ────────────────────────────────────────
    if gateway_client is None:
        result.refuse(NO_GATEWAY)
    else:
        snapshot = snapshots.load(GATEWAY_SCOPE, KIND_GATEWAY)
        mapped = [b for b in ((snapshot.payload if snapshot else {}) or {}).get(
            "backends", []) if b.get("cluster") == cluster]
        if not mapped:
            result.refuse(NO_BACKEND)
        else:
            try:
                live = {b.get("name"): bool(b.get("active"))
                        for b in gateway_client.list_backends()}
                result.checked_gateway_live = True
            except Exception as exc:  # noqa: BLE001 - any transport failure
                log.warning("benchmark guard could not read the Gateway: %s", exc)
                live = {}
                result.refuse(GATEWAY_UNREACHABLE)

            for backend in mapped:
                name = backend.get("name")
                # A backend the live list does not mention is not proof of
                # exclusion - it is proof that TMS and the Gateway disagree
                # about what exists.
                active = live.get(name)
                result.backends.append({"name": name, "active": active})
                if active is not False:
                    result.refuse(STILL_ROUTED if result.checked_gateway_live
                                  else GATEWAY_UNREACHABLE)

    # ── is it idle? ───────────────────────────────────────────────────
    if queries_envelope is None:
        snapshot = snapshots.load(cluster, KIND_QUERIES)
        if snapshot is None:
            result.refuse(NO_QUERY_VIEW)
            return result
        from tms.collector.snapshot import utcnow

        queries_envelope = {
            "stale": snapshot.is_stale(utcnow(), stale_threshold),
            "collected_at": snapshot.collected_at,
            "data": snapshot.payload,
        }

    if queries_envelope.get("stale"):
        result.refuse(STALE_QUERY_VIEW)
    result.observed_at = queries_envelope.get("collected_at")

    summary = ((queries_envelope.get("data") or {}).get("summary") or {})
    running = summary.get("running")
    if running is None:
        result.refuse(NO_QUERY_VIEW)
    else:
        result.running_queries = int(running)
        if int(running) > 0:
            result.refuse(QUERIES_RUNNING)

    return result
