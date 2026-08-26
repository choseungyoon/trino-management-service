"""Production protection. Non-negotiable.

> Running a benchmark against a cluster that is taking production traffic is
> itself an outage. Non-negotiable. — REQUIREMENTS.md

Two things follow from that sentence, and the second one is the easy one to
get wrong.

**TMS refuses; it does not arrange.** This module never deactivates anything.
CLAUDE.md is explicit that intake can only be stopped as step 1 of the safe
restart sequence, because an independent way to stop intake *is* the path
around absolute rule 5. A "run benchmark" button that quietly took a cluster
out of rotation would be exactly that button with a different label. So the
operator excludes the cluster - through the Gateway, or through a restart
sequence already in progress - and TMS checks their work.

**The check reads the Gateway, not TMS's snapshot.** A snapshot up to a poll
interval old can say "deactivated" about a backend that was re-activated
twenty seconds ago, and the whole guard would then be a description of the
past. The backend->cluster mapping still comes from the snapshot, because that
join is the only source of truth for which backend is which cluster; but
whether it is active is asked live, every time.

What the guard returns is recorded on the run (`benchmark_run.guard`). Six
months later that column is the only way to tell a comparable result from one
taken while production traffic was landing on the same coordinator.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: Why a run was refused. The screen renders these; they are not free text so
#: that a test can assert on the reason rather than on a sentence.
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
        "this cluster is still taking production traffic. It will not guess."),
    NO_BACKEND: (
        "No Gateway backend is matched to this cluster, so there is no way "
        "to tell whether it is in rotation."),
    GATEWAY_UNREACHABLE: (
        "The Gateway did not answer, so its routing state is unknown. An "
        "unknown routing state is not an excluded one."),
    # No internal rule numbers in this text: it is read by whoever is holding
    # the console, and "rule 5" tells them nothing about what to do next.
    STILL_ROUTED: (
        "This cluster is still in rotation. Exclude it first — through the "
        "Gateway, or as the first step of a safe restart. This console will "
        "not deactivate a backend on its own, because stopping traffic has to "
        "go through the restart sequence that drains queries first."),
    NO_QUERY_VIEW: (
        "This cluster's running queries have not been collected yet, so "
        "there is no way to tell whether the coordinator is idle."),
    STALE_QUERY_VIEW: (
        "The running-query view is stale. What it shows is the past, and the "
        "past does not say the cluster is idle now."),
    QUERIES_RUNNING: (
        "Queries are still running here. They would compete with the "
        "benchmark for the same workers, so the numbers would measure them "
        "as much as the cluster."),
}


class GuardResult:
    """What TMS checked, and whether it agrees to start.

    Kept as data rather than an exception so the same object can be shown on
    the screen *before* anyone presses anything - the operator should be able
    to see what is missing without submitting a request to find out.
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


def check(cluster: str, gateway_client, snapshots, stale_threshold: float,
          queries_envelope: Optional[Dict[str, Any]] = None) -> GuardResult:
    """Is it safe to benchmark `cluster` right now?

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
