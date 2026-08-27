"""Storage for benchmark runs and their measurements.

⛔ Append-only, the same grade as the audit log. The point of keeping these is
comparing today's numbers against numbers taken before something changed, and
a measurement that can be edited afterwards is not a measurement. There is no
delete: `benchmark_run.guard` records what made a run worth trusting.

Python 3.9 compatible.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tms.bench.runner import RUNNING, TERMINAL, UNKNOWN

log = logging.getLogger(__name__)

_RUN_COLUMNS = ("id", "cluster", "query_set", "label", "state", "reason", "actor",
                "repetitions", "guard", "queries", "started_at", "finished_at",
                "error")

_RESULT_COLUMNS = ("query_name", "iteration", "trino_query_id", "state", "elapsed_ms",
                   "trino_elapsed_ms", "trino_cpu_ms", "trino_queued_ms",
                   "trino_planning_ms", "processed_rows", "processed_bytes",
                   "peak_memory_bytes", "error", "occurred_at")


class BenchmarkStoreUnavailable(Exception):
    """Storage is not reachable.

    Blocking, unlike the work board's. A benchmark TMS cannot record is a
    cluster taken out of rotation for nothing.
    """


class ActiveRunExists(Exception):
    """This cluster already has a benchmark running."""


class InMemoryBenchmarkRepository:
    """For tests and the demo. Same interface, no durability."""

    def __init__(self):
        self.runs: List[Dict[str, Any]] = []
        self.results: Dict[Any, List[Dict[str, Any]]] = {}
        self._next = 1

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    def create(self, cluster, query_set, actor, roles, reason, repetitions,
               guard, label=None, queries=None, schedule_id=None):
        if any(r["state"] == RUNNING and r["cluster"] == cluster for r in self.runs):
            raise ActiveRunExists(cluster)
        run = {"id": self._next, "cluster": cluster, "query_set": query_set,
               "label": label, "state": RUNNING, "reason": reason, "actor": actor,
               "actor_roles": list(roles or []), "repetitions": int(repetitions),
               "guard": dict(guard or {}), "queries": list(queries or []),
               "schedule_id": schedule_id,
               "started_at": self._now(), "finished_at": None, "error": None}
        self._next += 1
        self.runs.append(run)
        self.results[run["id"]] = []
        return dict(run)

    def add_result(self, run_id, outcome):
        row = dict(outcome, occurred_at=self._now())
        self.results.setdefault(run_id, []).append(row)
        return row

    def finish(self, run_id, state, error=None):
        for run in self.runs:
            if run["id"] == run_id:
                run.update(state=state, error=error,
                           finished_at=(None if state == UNKNOWN else self._now()))

    def get(self, run_id):
        for run in self.runs:
            if str(run["id"]) == str(run_id):
                return dict(run, results=list(self.results.get(run["id"], [])),
                            is_terminal=run["state"] in TERMINAL)
        return None

    def recent(self, limit=20, cluster=None, query_set=None):
        rows = [r for r in self.runs
                if (cluster is None or r["cluster"] == cluster)
                and (query_set is None or r["query_set"] == query_set)]
        return [dict(r) for r in sorted(rows, key=lambda r: r["id"], reverse=True)[:limit]]

    def active(self, cluster=None):
        return [dict(r) for r in self.runs
                if r["state"] == RUNNING and (cluster is None or r["cluster"] == cluster)]

    def history_for_query(self, query_set, query_name, limit=100):
        by_run = {r["id"]: r for r in self.runs}
        rows = []
        for run_id, results in self.results.items():
            run = by_run.get(run_id)
            if run is None or run["query_set"] != query_set:
                continue
            for result in results:
                if result.get("query_name") != query_name:
                    continue
                rows.append(dict(result, run_id=run_id, cluster=run["cluster"],
                                 label=run.get("label"),
                                 run_started_at=run["started_at"]))
        rows.sort(key=lambda r: (r["run_id"], r.get("iteration") or 0), reverse=True)
        return rows[:limit]

    def reconcile_orphans(self):
        return 0


class PostgresBenchmarkRepository:
    """The real one."""

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "psycopg is required for PostgresBenchmarkRepository") from exc
        self._psycopg = psycopg
        self._dsn = dsn
        self._connection = psycopg.connect(dsn, autocommit=True)

    def _cursor(self):
        try:
            return self._connection.cursor()
        except Exception as exc:  # noqa: BLE001
            raise BenchmarkStoreUnavailable(str(exc))

    def create(self, cluster, query_set, actor, roles, reason, repetitions,
               guard, label=None, queries=None, schedule_id=None):
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "INSERT INTO benchmark_run"
                    " (cluster, query_set, label, state, reason, actor,"
                    "  actor_roles, repetitions, guard, queries, schedule_id)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,"
                    "         %s::jsonb, %s)"
                    " RETURNING id, started_at",
                    (cluster, query_set, label, RUNNING, reason, actor,
                     list(roles or []), int(repetitions), json.dumps(guard or {}),
                     json.dumps(list(queries or [])), schedule_id))
                row = cursor.fetchone()
        except self._psycopg.errors.UniqueViolation:
            # The partial unique index. Two runs on one cluster measure each
            # other, and neither number means anything afterwards.
            raise ActiveRunExists(cluster)
        except BenchmarkStoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BenchmarkStoreUnavailable(str(exc))
        return {"id": row[0], "cluster": cluster, "query_set": query_set,
                "label": label, "state": RUNNING, "reason": reason, "actor": actor,
                "repetitions": int(repetitions), "guard": dict(guard or {}),
                "queries": list(queries or []), "started_at": row[1],
                "finished_at": None, "error": None}

    def add_result(self, run_id, outcome):
        """One row per query execution. Failures are logged, never raised.

        This runs on the worker thread mid-run. Killing it because the database
        blinked would abandon the rest of the set on a cluster somebody took
        out of rotation to measure.
        """
        columns = ("run_id", "query_name", "iteration", "trino_query_id", "state",
                   "elapsed_ms", "trino_elapsed_ms", "trino_cpu_ms",
                   "trino_queued_ms", "trino_planning_ms", "processed_rows",
                   "processed_bytes", "peak_memory_bytes", "error")
        values = [run_id] + [outcome.get(c) for c in columns[1:]]
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "INSERT INTO benchmark_result ({}) VALUES ({})".format(
                        ", ".join(columns), ", ".join(["%s"] * len(columns))),
                    values)
        except Exception as exc:  # noqa: BLE001
            log.warning("dropping benchmark result for run %s: %s", run_id, exc)
        return outcome

    def finish(self, run_id, state, error=None):
        finished = "NULL" if state == UNKNOWN else "now()"
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "UPDATE benchmark_run"
                    "   SET state = %s, error = %s, updated_at = now(),"
                    "       finished_at = {}"
                    " WHERE id = %s".format(finished), (state, error, run_id))
        except Exception as exc:  # noqa: BLE001
            log.error("could not record the outcome of benchmark %s: %s", run_id, exc)

    def get(self, run_id) -> Optional[Dict[str, Any]]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT {} FROM benchmark_run WHERE id = %s".format(
                    ", ".join(_RUN_COLUMNS)), (run_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            run = _run(row)
            cursor.execute(
                "SELECT {} FROM benchmark_result WHERE run_id = %s"
                " ORDER BY id".format(", ".join(_RESULT_COLUMNS)), (run["id"],))
            run["results"] = [dict(zip(_RESULT_COLUMNS, r))
                              for r in cursor.fetchall() or []]
        return run

    def recent(self, limit=20, cluster=None, query_set=None) -> List[Dict[str, Any]]:
        sql = "SELECT {} FROM benchmark_run".format(", ".join(_RUN_COLUMNS))
        where, params = [], []
        if cluster:
            where.append("cluster = %s")
            params.append(cluster)
        if query_set:
            where.append("query_set = %s")
            params.append(query_set)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY started_at DESC, id DESC LIMIT %s"
        params.append(int(limit))
        with self._cursor() as cursor:
            cursor.execute(sql, params)
            return [_run(r) for r in cursor.fetchall() or []]

    def active(self, cluster=None) -> List[Dict[str, Any]]:
        return [r for r in self.recent(limit=50, cluster=cluster)
                if r["state"] == RUNNING]

    def history_for_query(self, query_set, query_name,
                          limit: int = 100) -> List[Dict[str, Any]]:
        """Every execution of one named query, newest first.

        Scoped to the set: `q1` in two sets are different statements. The
        statement is not joined in - `benchmark_run.queries` holds the copy
        that is true for each row; `benchmark_query` holds today's.
        """
        columns = ("run_id", "query_name", "iteration", "trino_query_id", "state",
                   "elapsed_ms", "trino_elapsed_ms", "trino_cpu_ms",
                   "trino_queued_ms", "processed_rows", "processed_bytes",
                   "peak_memory_bytes", "error", "occurred_at")
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "SELECT {},"
                    "       r.cluster, r.label, r.started_at"
                    "  FROM benchmark_result b"
                    "  JOIN benchmark_run r ON r.id = b.run_id"
                    " WHERE r.query_set = %s AND b.query_name = %s"
                    " ORDER BY b.run_id DESC, b.iteration DESC"
                    " LIMIT %s".format(", ".join("b." + c for c in columns)),
                    (query_set, query_name, int(limit)))
                rows = cursor.fetchall() or []
        except BenchmarkStoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BenchmarkStoreUnavailable(str(exc))
        names = columns + ("cluster", "label", "run_started_at")
        return [dict(zip(names, row)) for row in rows]

    def reconcile_orphans(self) -> int:
        """Mark runs left RUNNING by a previous process as UNKNOWN.

        Called once at startup. Such a row describes a worker thread that no
        longer exists, and it blocks the cluster's unique index forever.

        UNKNOWN rather than FAILED: the measurements already written are real;
        what is unknown is whether the rest of the set ran.
        """
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "UPDATE benchmark_run SET state = %s, updated_at = now(),"
                    "       error = COALESCE(error,"
                    "         'tms-api restarted while this run was in flight,"
                    " so the set may be incomplete. Compare with care.')"
                    " WHERE state = %s", (UNKNOWN, RUNNING))
                return cursor.rowcount or 0
        except Exception as exc:  # noqa: BLE001
            log.warning("could not reconcile orphaned benchmark runs: %s", exc)
            return 0


def _run(row) -> Dict[str, Any]:
    run = dict(zip(_RUN_COLUMNS, row))
    guard = run.get("guard")
    run["guard"] = guard if isinstance(guard, dict) else json.loads(guard or "{}")
    queries = run.get("queries")
    run["queries"] = queries if isinstance(queries, list) else json.loads(queries or "[]")
    run["is_terminal"] = run["state"] in TERMINAL
    return run
