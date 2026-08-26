"""Comparing two runs.

The question this exists for is "why is cluster A slower than cluster B", so
the output is per query, not one headline number. A single aggregate would let
one pathological query swing the verdict and hide the nine that are identical -
and the pathological one is usually the finding.

Three rules, each of which was a way to produce a confident wrong answer:

* **Only the same query set may be compared.** Two different sets sharing a
  query name are not the same query.
* **The median, not the mean.** One cold-start outlier moves a mean of three
  runs by a third. `repetitions` exists precisely because the first execution
  of a query is not like the rest.
* **A run whose guard failed is flagged, loudly.** A measurement taken while
  the cluster was still in rotation is a measurement of production traffic.
  It is kept - deleting evidence is worse - but nothing here lets it pass as
  comparable without saying so.

Python 3.9 compatible.
"""

from typing import Any, Dict, List, Optional

#: Below this, a difference is noise dressed as a finding. Chosen as a
#: percentage rather than an absolute so it means the same thing for a 200ms
#: query and a 200s one.
NOISE_PERCENT = 5.0

FASTER = "faster"
SLOWER = "slower"
SAME = "same"
ONLY_BASELINE = "only_baseline"
ONLY_CANDIDATE = "only_candidate"


class NotComparable(Exception):
    """These two runs cannot be put side by side."""


def median(values: List[float]) -> Optional[float]:
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def summarise_run(run: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per query: how many ran, how many failed, and the median wall time.

    Failures are excluded from the timing but counted, because a query that
    fails fast would otherwise look like the fastest query in the set.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for result in run.get("results") or []:
        name = result.get("query_name")
        entry = grouped.setdefault(name, {"name": name, "runs": 0, "failures": 0,
                                          "elapsed": [], "cpu": [], "rows": None})
        entry["runs"] += 1
        if result.get("state") == "FAILED":
            entry["failures"] += 1
            continue
        entry["elapsed"].append(result.get("elapsed_ms"))
        if result.get("trino_cpu_ms") is not None:
            entry["cpu"].append(result.get("trino_cpu_ms"))
        if entry["rows"] is None:
            entry["rows"] = result.get("processed_rows")

    for entry in grouped.values():
        entry["median_ms"] = median(entry["elapsed"])
        entry["median_cpu_ms"] = median(entry["cpu"])
        entry["fastest_ms"] = min(entry["elapsed"]) if entry["elapsed"] else None
    return grouped


def compare(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Two runs, side by side, per query."""
    if baseline.get("query_set") != candidate.get("query_set"):
        raise NotComparable(
            "These runs used different query sets ({} and {}). The same query "
            "name in two sets is not the same query.".format(
                baseline.get("query_set"), candidate.get("query_set")))
    if str(baseline.get("id")) == str(candidate.get("id")):
        raise NotComparable("That is the same run on both sides.")

    left = summarise_run(baseline)
    right = summarise_run(candidate)
    edited = _edited_between(baseline, candidate)

    rows: List[Dict[str, Any]] = []
    for name in sorted(set(left) | set(right)):
        a, b = left.get(name), right.get(name)
        row: Dict[str, Any] = {
            "name": name,
            "baseline": a,
            "candidate": b,
            "delta_ms": None,
            "delta_percent": None,
            "verdict": SAME,
        }
        # ⛔ Same set, same name, different SQL. A set can now be
        # edited between two runs, and without this the table would show a
        # confident percentage for two different statements.
        row["statement_changed"] = name in edited
        if a is None:
            row["verdict"] = ONLY_CANDIDATE
        elif b is None:
            row["verdict"] = ONLY_BASELINE
        elif a.get("median_ms") is None or b.get("median_ms") is None:
            # Every execution failed on one side. There is no time to compare,
            # and calling that "same" would bury a total failure in a table of
            # small differences.
            row["verdict"] = ONLY_BASELINE if b.get("median_ms") is None else ONLY_CANDIDATE
        else:
            row["delta_ms"] = b["median_ms"] - a["median_ms"]
            row["delta_percent"] = (
                (b["median_ms"] - a["median_ms"]) / a["median_ms"] * 100.0
                if a["median_ms"] else None)
            if row["delta_percent"] is None or abs(row["delta_percent"]) < NOISE_PERCENT:
                row["verdict"] = SAME
            else:
                row["verdict"] = SLOWER if row["delta_ms"] > 0 else FASTER
        rows.append(row)

    compared = [r for r in rows if r["delta_percent"] is not None]
    return {
        "query_set": baseline.get("query_set"),
        "baseline": _side(baseline),
        "candidate": _side(candidate),
        "rows": rows,
        "summary": {
            "queries": len(rows),
            "faster": sum(1 for r in rows if r["verdict"] == FASTER),
            "slower": sum(1 for r in rows if r["verdict"] == SLOWER),
            "same": sum(1 for r in rows if r["verdict"] == SAME),
            "unmatched": sum(1 for r in rows
                             if r["verdict"] in (ONLY_BASELINE, ONLY_CANDIDATE)),
            # The median of the per-query differences, not the difference of
            # the totals: a total is dominated by whichever query is longest.
            "median_delta_percent": median([r["delta_percent"] for r in compared]),
        },
        "warnings": _warnings(baseline, candidate),
    }


def _edited_between(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> set:
    """Query names whose SQL differs between the two runs' snapshots.

    Empty when either run kept no snapshot - runs from before 018, whose
    `queries` column defaulted to empty. "Cannot tell" and "did not change"
    are different answers and only one of them is honest here.
    """
    left = _statements(baseline)
    right = _statements(candidate)
    if not left or not right:
        return set()
    return {name for name in set(left) & set(right) if left[name] != right[name]}


def _statements(run: Dict[str, Any]) -> Dict[str, str]:
    return {q.get("name"): q.get("sql") for q in (run.get("queries") or [])
            if q.get("name")}


def _side(run: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": run.get("id"), "cluster": run.get("cluster"),
            "label": run.get("label"), "state": run.get("state"),
            "repetitions": run.get("repetitions"),
            "started_at": run.get("started_at"),
            "guard_ok": bool((run.get("guard") or {}).get("ok"))}


def _warnings(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    for label, run in (("baseline", baseline), ("candidate", candidate)):
        if not (run.get("guard") or {}).get("ok"):
            warnings.append(
                "The {} run (#{}) was taken while TMS could not confirm the "
                "cluster was out of rotation and idle. Treat its numbers as "
                "measurements of whatever else was happening.".format(
                    label, run.get("id")))
        if run.get("state") not in ("SUCCEEDED",):
            warnings.append(
                "The {} run (#{}) ended {}, so its set may be "
                "incomplete.".format(label, run.get("id"), run.get("state")))
    edited = sorted(_edited_between(baseline, candidate))
    if edited:
        warnings.append(
            "The statement changed between these two runs for: {}. Those rows "
            "compare two different queries, not two runs of one.".format(
                ", ".join(edited)))
    if baseline.get("repetitions") != candidate.get("repetitions"):
        warnings.append(
            "The two runs used different repetition counts ({} and {}). The "
            "medians are still comparable, but one is a median of fewer "
            "samples.".format(baseline.get("repetitions"),
                              candidate.get("repetitions")))
    return warnings
