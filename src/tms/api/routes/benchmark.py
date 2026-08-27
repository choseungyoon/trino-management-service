"""Benchmark: runs, comparisons, and the query sets they execute.

Eleven endpoints, the largest single feature. Shapes follow what the service
already returns - the service owns the rules (the read-only allowlist, the
reason/audit/admin requirement, refusing a second run on one cluster), and
these routes only carry them over HTTP.

⛔ Nothing here returns a result row. A run gives back timings; the rows Trino
produced are counted and discarded. That is the line between this and a SQL
editor, and it is a property of the API, not just of a screen.

Python 3.9 compatible.
"""

from typing import Any, Dict, Optional

from tms.api.routes.deps import Deps


def register(app, deps: Deps) -> None:
    from fastapi import Body, Depends, Query

    from tms.api.permissions import Principal

    principal_of = deps.current_principal

    def bench():
        return deps.require("benchmark")

    # ------------------------------------------------------------ reading

    @app.get("/api/v1/benchmark")
    def benchmark_overview(principal: Principal = Depends(principal_of)):
        """Every cluster with its readiness, the query sets, and recent runs."""
        return bench().overview(principal)

    @app.get("/api/v1/benchmark/sets")
    def list_sets(principal: Principal = Depends(principal_of)):
        return bench().sets(principal)

    @app.get("/api/v1/benchmark/sets/{key}")
    def get_set(key: str, principal: Principal = Depends(principal_of)):
        return bench().query_set(principal, key)

    @app.get("/api/v1/benchmark/sets/{key}/queries/{name}/history")
    def query_history(key: str, name: str, limit: int = Query(100, ge=1, le=1000),
                      bucket: str = Query("run"),
                      principal: Principal = Depends(principal_of)):
        """Every execution of one query, with the statement each run used.

        `current` is what the query says today; each row's `statement` is what
        that run executed. Where they differ the numbers are not comparable,
        and `changed` says so without the caller having to diff them.

        `bucket` groups the chart's x axis: `run`, `day` or `month`. A query
        that runs several times a day is noise at one dot per run. An
        unrecognised value falls back to `run` rather than refusing - this is
        a query string, and a chart that will not draw teaches nothing.
        """
        return bench().query_history(principal, key, name, limit=limit,
                                     bucket=bucket)

    @app.get("/api/v1/benchmarks/{run_id}")
    def get_run(run_id: str, principal: Principal = Depends(principal_of)):
        return bench().run(principal, run_id)

    @app.get("/api/v1/benchmarks/{run_id}/comparable")
    def comparable(run_id: str, principal: Principal = Depends(principal_of)):
        """Finished runs of the same set - what may go on the other side.

        Filtered here rather than by the caller: offering a run of a different
        set as a comparison choice is offering a wrong answer.
        """
        service = bench()
        run = service.run(principal, run_id)
        return {"runs": service.comparable_runs(principal, run)}

    @app.get("/api/v1/benchmarks/{baseline_id}/compare/{candidate_id}")
    def compare(baseline_id: str, candidate_id: str,
                principal: Principal = Depends(principal_of)):
        return bench().compare(principal, baseline_id, candidate_id)

    # ------------------------------------------------------------ writing

    @app.post("/api/v1/benchmark", status_code=201)
    def start(body: Dict[str, Any] = Body(...),
              principal: Principal = Depends(principal_of)):
        """Start one set on one or more clusters.

        Partial success is a normal outcome: `started` and `refused` both come
        back, because cancelling the whole request over one busy cluster would
        mean re-running the rest later, on clusters whose caches are now warm.
        """
        return bench().start_many(
            principal,
            clusters=list(body.get("clusters") or []),
            query_set=str(body.get("query_set") or ""),
            reason=body.get("reason"),
            repetitions=_int(body.get("repetitions"), 1),
            label=body.get("label"))

    @app.post("/api/v1/benchmarks/{run_id}/abort")
    def abort(run_id: str, principal: Principal = Depends(principal_of)):
        """Stop after the query in flight.

        No reason and no audit record: stopping is the absence of further
        action, and the run row already carries who started it and why.
        """
        return bench().abort(principal, run_id)

    @app.post("/api/v1/benchmark/sets", status_code=201)
    def create_set(body: Dict[str, Any] = Body(...),
                   principal: Principal = Depends(principal_of)):
        """A set and its first query, together.

        One call, because a set with no queries is a set that cannot be run.
        """
        return bench().create_set(
            principal,
            key=str(body.get("key") or "").strip(),
            title=body.get("title") or "",
            description=body.get("description") or "",
            name=str(body.get("name") or "").strip(),
            statement=body.get("statement") or "",
            reason=body.get("reason"))

    @app.put("/api/v1/benchmark/sets/{key}")
    def update_set(key: str, body: Dict[str, Any] = Body(...),
                   principal: Principal = Depends(principal_of)):
        """Title and description only. ⛔ The key never changes - runs are
        recorded against it and only runs of the same key can be compared."""
        return bench().save_set(principal, key=key, title=body.get("title") or "",
                                description=body.get("description") or "",
                                reason=body.get("reason"))

    @app.delete("/api/v1/benchmark/sets/{key}", status_code=204)
    def delete_set(key: str, reason: Optional[str] = Query(None),
                   principal: Principal = Depends(principal_of)):
        """Past runs and their measurements are untouched - they hold the set
        by value, not by reference."""
        bench().delete_set(principal, key, reason=reason)
        return None

    @app.put("/api/v1/benchmark/sets/{key}/queries/{name}")
    def save_query(key: str, name: str, body: Dict[str, Any] = Body(...),
                   principal: Principal = Depends(principal_of)):
        """Create or replace one query.

        `previous_name` renames rather than adding a second row. Absent, this
        is an insert.
        """
        return bench().save_query(
            principal, key, name=name, title=body.get("title") or "",
            statement=body.get("statement") or "", reason=body.get("reason"),
            position=_int(body.get("position"), 0),
            original_name=(body.get("previous_name") or None))

    @app.delete("/api/v1/benchmark/sets/{key}/queries/{name}", status_code=204)
    def delete_query(key: str, name: str, reason: Optional[str] = Query(None),
                     principal: Principal = Depends(principal_of)):
        bench().delete_query(principal, key, name, reason=reason)
        return None


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
