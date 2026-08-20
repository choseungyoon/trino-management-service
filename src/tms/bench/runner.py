"""Executing a query set (FR-BM-01).

Runs on a worker thread, like a fleet job, because a set of ten queries at
thirty seconds each outlives any request. What it writes as it goes is the
point: a run that dies half-way still leaves the measurements it already took,
and a partial answer to "is A slower than B" beats no answer.

**A failure is a result, not the end of the run.** A benchmark whose third
query fails on cluster B and stops has measured nothing about queries four
through ten - which are exactly the ones somebody would then have to run by
hand. Failures are recorded with their elapsed time and Trino query id and the
run continues; the run itself only fails when nothing could be executed at all.

Python 3.9 compatible.
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
ABORTED = "ABORTED"
RUNNING = "RUNNING"
UNKNOWN = "UNKNOWN"
TERMINAL = (SUCCEEDED, FAILED, ABORTED, UNKNOWN)

#: Fields kept from Trino's `stats`, mapped to the columns in 016. Verified
#: against Trino 477's client protocol - see clients/sql.py execute().
STAT_COLUMNS = (
    ("trino_elapsed_ms", "elapsedTimeMillis"),
    ("trino_cpu_ms", "cpuTimeMillis"),
    ("trino_queued_ms", "queuedTimeMillis"),
    ("trino_planning_ms", "planningTimeMillis"),
    ("processed_rows", "processedRows"),
    ("processed_bytes", "processedBytes"),
    ("peak_memory_bytes", "peakMemoryBytes"),
)


def measurements(stats: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Trino's stats -> the columns 016 stores.

    Absent keys stay None rather than becoming 0: a query that failed during
    planning produced no CPU time, and a 0 there would read as "it used no
    CPU" instead of "nobody measured".
    """
    stats = stats or {}
    out: Dict[str, Any] = {}
    for column, key in STAT_COLUMNS:
        value = stats.get(key)
        out[column] = int(value) if isinstance(value, (int, float)) else None
    return out


class BenchmarkRunner:
    """Executes one run to completion on a worker thread."""

    def __init__(self, sql_client_factory: Callable[[str], Any],
                 repository, pause_seconds: float = 0.0,
                 sleep=time.sleep) -> None:
        self._sql_for = sql_client_factory
        self._repository = repository
        self._pause = pause_seconds
        self._sleep = sleep
        self._aborting: Dict[Any, bool] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- control

    def abort(self, run_id: Any) -> None:
        """Ask the worker to stop after the query it is running.

        Deliberately not a kill. TMS cancelling its own statement mid-flight
        would leave a query on the coordinator whose fate nobody recorded, and
        the benchmark's own queries are the one workload that is guaranteed to
        finish on its own - the cluster is out of rotation and idle.
        """
        with self._lock:
            self._aborting[run_id] = True

    def _should_abort(self, run_id: Any) -> bool:
        with self._lock:
            return bool(self._aborting.get(run_id))

    def _forget(self, run_id: Any) -> None:
        with self._lock:
            self._aborting.pop(run_id, None)

    # --------------------------------------------------------------- work

    def start(self, run: Dict[str, Any], query_set, repetitions: int) -> threading.Thread:
        thread = threading.Thread(
            target=self._run, args=(run, query_set, repetitions),
            name="benchmark-{}".format(run["id"]), daemon=True)
        thread.start()
        return thread

    def _run(self, run: Dict[str, Any], query_set, repetitions: int) -> None:
        run_id = run["id"]
        cluster = run["cluster"]
        executed = 0
        failures = 0
        try:
            client = self._sql_for(cluster)
        except Exception as exc:  # noqa: BLE001
            log.exception("benchmark %s could not open a SQL client", run_id)
            self._repository.finish(run_id, FAILED, error=str(exc))
            self._forget(run_id)
            return

        try:
            for iteration in range(1, int(repetitions) + 1):
                for query in query_set.queries:
                    if self._should_abort(run_id):
                        self._repository.finish(
                            run_id, ABORTED,
                            error="Stopped on request after {} quer{}.".format(
                                executed, "y" if executed == 1 else "ies"))
                        return
                    outcome = self._one(client, query, iteration)
                    executed += 1
                    if outcome["state"] == FAILED:
                        failures += 1
                    self._repository.add_result(run_id, outcome)
                    if self._pause:
                        # A gap between statements so the cluster is not
                        # measured while it is still cleaning up the last one.
                        self._sleep(self._pause)

            if executed and failures == executed:
                # Every single query failed. Calling that SUCCEEDED because the
                # loop completed would put a run full of errors into a
                # comparison as though it were data.
                self._repository.finish(
                    run_id, FAILED,
                    error="Every query failed. See the results for why.")
            else:
                self._repository.finish(run_id, SUCCEEDED)
        except Exception as exc:  # noqa: BLE001
            log.exception("benchmark %s failed", run_id)
            self._repository.finish(run_id, FAILED, error=str(exc))
        finally:
            self._forget(run_id)

    def _one(self, client, query, iteration: int) -> Dict[str, Any]:
        from tms.clients.errors import TrinoClientError
        from tms.clients.sql import QueryFailed

        started = time.monotonic()
        try:
            result = client.execute(query.sql)
        except QueryFailed as exc:
            outcome = {"query_name": query.name, "iteration": iteration,
                       "state": FAILED, "trino_query_id": getattr(exc, "query_id", None),
                       "elapsed_ms": getattr(exc, "elapsed_ms", None) or _ms(started),
                       "error": str(exc)}
            outcome.update(measurements(None))
            return outcome
        except TrinoClientError as exc:
            outcome = {"query_name": query.name, "iteration": iteration,
                       "state": FAILED, "trino_query_id": None,
                       "elapsed_ms": _ms(started), "error": str(exc)}
            outcome.update(measurements(None))
            return outcome

        outcome = {"query_name": query.name, "iteration": iteration,
                   "state": SUCCEEDED, "trino_query_id": result.get("query_id"),
                   "elapsed_ms": result.get("elapsed_ms") or _ms(started),
                   "error": None}
        outcome.update(measurements(result.get("stats")))
        return outcome


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
