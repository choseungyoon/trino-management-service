"""Benchmark results over time, aggregated for a chart.

⛔ Per bucket, not per execution. Plotting every repetition draws the warm-up
as a spike on every run and buries what the chart is for, which is whether this
query is drifting. Each point is the median of what fell in its bucket; the
spread is in the summary beside it.

No pixels here - the client draws. What is decided here is what the numbers
mean: which executions group together, what the middle of a group is, and
whether there is enough to call a trend at all.

⛔ Every series shares one bucket axis. Series used to be indexed by position
within themselves, so a cluster with four runs and a cluster with two were
drawn across the same width - the second one's last point sat under the first
one's second point, and the chart said they were measured at the same time.

Python 3.9 compatible.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

#: How executions are grouped along the x axis.
BY_RUN = "run"
BY_DAY = "day"
BY_MONTH = "month"
BUCKETS = (BY_RUN, BY_DAY, BY_MONTH)

#: What each bucket is called on screen. Sent with the data so the axis label
#: and the tooltip cannot disagree with the grouping that produced them.
BUCKET_LABELS = {
    BY_RUN: "Every run",
    BY_DAY: "Daily",
    BY_MONTH: "Monthly",
}


def summarise(values: Sequence[float]) -> Dict[str, Any]:
    """Average and median together, on purpose.

    Comparison judges on the median, because one cold first execution drags a
    mean. The mean is still worth showing beside it: when the two disagree,
    that gap *is* the finding - it says the distribution has a tail.
    """
    numbers = sorted(v for v in values if v is not None)
    if not numbers:
        return {"count": 0, "avg": None, "median": None, "min": None, "max": None}
    middle = len(numbers) // 2
    median = (numbers[middle] if len(numbers) % 2
              else (numbers[middle - 1] + numbers[middle]) / 2.0)
    return {
        "count": len(numbers),
        "avg": sum(numbers) / float(len(numbers)),
        "median": median,
        "min": numbers[0],
        "max": numbers[-1],
    }


def _moment(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _bucket_key(run_id: Any, at: Any, bucket: str):
    """What groups together, and what orders the axis.

    Returns (sort_key, label). A run with no timestamp keeps its own bucket
    rather than being folded into a neighbour's - "when" is missing, not zero.
    """
    moment = _moment(at)
    if bucket == BY_DAY and moment is not None:
        return moment.strftime("%Y-%m-%d")
    if bucket == BY_MONTH and moment is not None:
        return moment.strftime("%Y-%m")
    return "run:{}".format(run_id)


def build(history: List[Dict[str, Any]], bucket: str = BY_RUN) -> Dict[str, Any]:
    """History rows -> one series per cluster on a shared bucket axis."""
    if bucket not in BUCKETS:
        bucket = BY_RUN

    # (cluster, bucket) -> the executions that fell in it.
    cells: Dict[Any, Dict[str, Any]] = {}
    axis: Dict[str, Dict[str, Any]] = {}

    for entry in history or []:
        if entry.get("state") == "FAILED":
            continue
        if entry.get("elapsed_ms") is None:
            continue
        at = entry.get("run_started_at")
        key = _bucket_key(entry.get("run_id"), at, bucket)

        slot = axis.setdefault(key, {"key": key, "at": at, "runs": set()})
        # The earliest reading in a bucket dates it. A day bucket labelled by
        # whichever row happened to arrive last would move as more runs land.
        if _moment(at) is not None and (
                _moment(slot["at"]) is None or _moment(at) < _moment(slot["at"])):
            slot["at"] = at
        slot["runs"].add(entry.get("run_id"))

        cell = cells.setdefault((entry.get("cluster"), key), {
            "cluster": entry.get("cluster"), "bucket": key,
            "at": at, "elapsed": [], "runs": set(),
        })
        cell["elapsed"].append(entry["elapsed_ms"])
        cell["runs"].add(entry.get("run_id"))
        if _moment(at) is not None and (
                _moment(cell["at"]) is None or _moment(at) < _moment(cell["at"])):
            cell["at"] = at

    # Oldest first: a chart of time that runs right-to-left is a trap.
    ordered = sorted(axis.values(),
                     key=lambda s: (_moment(s["at"]) is None, _moment(s["at"]) or 0,
                                    s["key"]))
    positions = {slot["key"]: index for index, slot in enumerate(ordered)}

    by_cluster: Dict[str, List[Dict[str, Any]]] = {}
    for cell in cells.values():
        by_cluster.setdefault(cell["cluster"], []).append(cell)

    series, summaries = [], []
    for cluster in sorted(by_cluster):
        points = []
        for cell in sorted(by_cluster[cluster], key=lambda c: positions[c["bucket"]]):
            stats = summarise(cell["elapsed"])
            points.append({
                "x": positions[cell["bucket"]],
                "bucket": cell["bucket"],
                "at": _iso(cell["at"]),
                # ⛔ The plotted value is the median, the same choice the
                # comparison screen makes. `avg_ms` rides along so a tooltip
                # can show the gap without a second request.
                "median_ms": stats["median"],
                "avg_ms": stats["avg"],
                "runs": len(cell["runs"]),
                "executions": stats["count"],
            })
        every = [ms for cell in by_cluster[cluster] for ms in cell["elapsed"]]
        plotted = [p["median_ms"] for p in points]
        series.append({
            "cluster": cluster,
            "points": points,
            # The horizontal reference line. The mean of what is *drawn*, not
            # of every execution - a line computed from a different population
            # than the dots is a line that lies about them.
            "mean_of_points": (sum(plotted) / float(len(plotted))) if plotted else None,
        })
        summaries.append(dict(summarise(every), cluster=cluster,
                              runs=len({r for c in by_cluster[cluster]
                                        for r in c["runs"]}),
                              buckets=len(points)))

    return {
        "bucket": bucket,
        "bucket_label": BUCKET_LABELS[bucket],
        "buckets": [{"key": s["key"], "at": _iso(s["at"]), "runs": len(s["runs"])}
                    for s in ordered],
        "series": series,
        "summaries": summaries,
        # ⛔ Two clusters measured once each is two dots and no line - a chart
        # pretending to be a trend. The summaries below say the same thing
        # without the pretence, so the caller is told not to draw.
        "drawable": any(len(s["points"]) >= 2 for s in series),
    }


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else value
