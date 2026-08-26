"""Benchmark results over time, aggregated for a chart.

⛔ Per run, not per execution. Plotting every repetition draws the warm-up as
a spike on every run and buries what the chart is for, which is whether this
query is drifting. Each point is that run's median; the spread is in the
summary beside it.

No pixels here - the client draws. What is decided here is what the numbers
mean: which runs group together, what the middle of a run is, and whether
there is enough to call a trend at all.

Python 3.9 compatible.
"""

from typing import Any, Dict, List, Optional, Sequence


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


def build(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """History rows -> one series per cluster, plus per-cluster summaries."""
    runs: Dict[Any, Dict[str, Any]] = {}
    for entry in history or []:
        if entry.get("state") == "FAILED":
            continue
        run = runs.setdefault(entry.get("run_id"), {
            "run_id": entry.get("run_id"),
            "cluster": entry.get("cluster"),
            "at": entry.get("run_started_at"),
            "elapsed": [],
        })
        if entry.get("elapsed_ms") is not None:
            run["elapsed"].append(entry["elapsed_ms"])

    by_cluster: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs.values():
        if run["elapsed"]:
            by_cluster.setdefault(run["cluster"], []).append(run)

    series, summaries = [], []
    for cluster in sorted(by_cluster):
        # Oldest first: a chart of time that runs right-to-left is a trap.
        ordered = sorted(by_cluster[cluster],
                         key=lambda r: (r["at"] is None, r["at"], r["run_id"]))
        points = []
        for run in ordered:
            stats = summarise(run["elapsed"])
            points.append({
                "run_id": run["run_id"],
                "at": _iso(run["at"]),
                "median_ms": stats["median"],
                "repetitions": stats["count"],
            })
        series.append({"cluster": cluster, "points": points})
        every = [ms for run in ordered for ms in run["elapsed"]]
        summaries.append(dict(summarise(every), cluster=cluster, runs=len(ordered)))

    return {
        "series": series,
        "summaries": summaries,
        # ⛔ Two clusters measured once each is two dots and no line - a chart
        # pretending to be a trend. The summaries below say the same thing
        # without the pretence, so the caller is told not to draw.
        "drawable": any(len(s["points"]) >= 2 for s in series),
    }


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else value
