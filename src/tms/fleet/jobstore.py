"""Storage for fleet job runs (FR-FL-05).

A job's subprocess is a child of tms-api, so it dies when tms-api does. Without
a row somewhere there would be no trace that a scale-out was half-finished when
the service was restarted - and half-provisioned nodes are exactly the thing
someone needs to find out about later.

Two rules, both enforced by the schema in 012 rather than here:

* one running job per cluster (partial unique index),
* output is append-only, like the audit log and the restart progress log.

There is no `delete`. A run that should not have happened is still a run that
happened.

Python 3.9 compatible.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tms.fleet.jobs import RUNNING, TERMINAL, UNKNOWN

log = logging.getLogger(__name__)


class JobStoreUnavailable(Exception):
    """Storage is not reachable.

    Blocking, like the restart sequence store: starting a job TMS cannot record
    produces exactly the untracked change this table exists to prevent.
    """


class ActiveJobExists(Exception):
    """This cluster already has a job running."""


class InMemoryJobRepository:
    """For tests and the demo. Same interface, no durability."""

    def __init__(self):
        self.runs: List[Dict[str, Any]] = []
        self.output: Dict[Any, List[Dict[str, Any]]] = {}
        self._next = 1

    def create(self, cluster, job, actor, roles, reason, parameters):
        if any(r["state"] == RUNNING and r["cluster"] == cluster for r in self.runs):
            raise ActiveJobExists(cluster)
        run = {"id": self._next, "cluster": cluster, "job": job, "actor": actor,
               "actor_roles": list(roles or []), "reason": reason,
               "parameters": dict(parameters or {}), "state": RUNNING,
               "exit_code": None, "error": None,
               # A real timestamp even in the fake: the screen renders "never"
               # for None, which reads as a job that failed to start.
               "started_at": datetime.now(timezone.utc), "finished_at": None}
        self._next += 1
        self.runs.append(run)
        self.output[run["id"]] = []
        return run

    def append_output(self, run_id, message, level="output"):
        self.output.setdefault(run_id, []).append({"level": level, "message": message})

    def finish(self, run_id, state, exit_code=None, error=None):
        for run in self.runs:
            if run["id"] == run_id:
                run.update(state=state, exit_code=exit_code, error=error,
                           finished_at=(None if state == UNKNOWN
                                        else datetime.now(timezone.utc)))

    def get(self, run_id):
        for run in self.runs:
            if str(run["id"]) == str(run_id):
                return dict(run, output=list(self.output.get(run["id"], [])),
                            is_terminal=run["state"] in TERMINAL)
        return None

    def recent(self, limit=20, cluster=None):
        rows = [r for r in self.runs if cluster is None or r["cluster"] == cluster]
        return [dict(r) for r in sorted(rows, key=lambda r: r["id"], reverse=True)[:limit]]

    def active(self, cluster=None):
        return [dict(r) for r in self.runs
                if r["state"] == RUNNING and (cluster is None or r["cluster"] == cluster)]

    def reconcile_orphans(self):
        return 0


class PostgresJobRepository:
    """The real one."""

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "psycopg is required for PostgresJobRepository") from exc
        self._psycopg = psycopg
        self._dsn = dsn
        self._connection = psycopg.connect(dsn, autocommit=True)

    def _cursor(self):
        try:
            return self._connection.cursor()
        except Exception as exc:  # noqa: BLE001
            raise JobStoreUnavailable(str(exc))

    def create(self, cluster, job, actor, roles, reason, parameters):
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "INSERT INTO fleet_job_run"
                    " (cluster, job, state, reason, actor, actor_roles, parameters)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)"
                    " RETURNING id, started_at",
                    (cluster, job, RUNNING, reason, actor, list(roles or []),
                     json.dumps(parameters or {})))
                row = cursor.fetchone()
        except self._psycopg.errors.UniqueViolation:
            # The partial unique index, not a check here: two scale-outs racing
            # on the same inventory is not a conflict to resolve afterwards.
            raise ActiveJobExists(cluster)
        except JobStoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise JobStoreUnavailable(str(exc))
        return {"id": row[0], "cluster": cluster, "job": job, "actor": actor,
                "reason": reason, "parameters": dict(parameters or {}),
                "state": RUNNING, "started_at": row[1],
                "finished_at": None, "exit_code": None, "error": None}

    def append_output(self, run_id, message, level="output"):
        """One row per line. Failures are logged, never raised.

        This runs on the job's worker thread while a playbook is mid-flight.
        Killing that thread because the database blinked would abandon a running
        change to production and lose the rest of its output.
        """
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "INSERT INTO fleet_job_output (run_id, level, message)"
                    " VALUES (%s, %s, %s)", (run_id, level, message))
        except Exception as exc:  # noqa: BLE001
            log.warning("dropping fleet job output for run %s: %s", run_id, exc)

    def finish(self, run_id, state, exit_code=None, error=None):
        # UNKNOWN carries no finish time: nobody can say when, or whether, the
        # playbook stopped. The schema enforces the same thing.
        finished = "NULL" if state == UNKNOWN else "now()"
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "UPDATE fleet_job_run"
                    "   SET state = %s, exit_code = %s, error = %s,"
                    "       updated_at = now(), finished_at = {}"
                    " WHERE id = %s".format(finished),
                    (state, exit_code, error, run_id))
        except Exception as exc:  # noqa: BLE001
            log.error("could not record the outcome of fleet job %s: %s", run_id, exc)

    def get(self, run_id) -> Optional[Dict[str, Any]]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT id, cluster, job, state, reason, actor, parameters,"
                "       started_at, finished_at, exit_code, error"
                "  FROM fleet_job_run WHERE id = %s", (run_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            run = _run(row)
            cursor.execute(
                "SELECT level, message, occurred_at FROM fleet_job_output"
                " WHERE run_id = %s ORDER BY id", (run["id"],))
            run["output"] = [{"level": r[0], "message": r[1], "at": r[2]}
                             for r in cursor.fetchall() or []]
        return run

    def recent(self, limit=20, cluster=None) -> List[Dict[str, Any]]:
        sql = ("SELECT id, cluster, job, state, reason, actor, parameters,"
               "       started_at, finished_at, exit_code, error"
               "  FROM fleet_job_run")
        params: List[Any] = []
        if cluster:
            sql += " WHERE cluster = %s"
            params.append(cluster)
        sql += " ORDER BY started_at DESC, id DESC LIMIT %s"
        params.append(int(limit))
        with self._cursor() as cursor:
            cursor.execute(sql, params)
            return [_run(r) for r in cursor.fetchall() or []]

    def active(self, cluster=None) -> List[Dict[str, Any]]:
        return [r for r in self.recent(limit=50, cluster=cluster)
                if r["state"] == RUNNING]

    def reconcile_orphans(self) -> int:
        """Mark runs left RUNNING by a previous process as UNKNOWN.

        Called once at startup. A row still saying RUNNING after tms-api has
        restarted describes a subprocess that no longer exists, and leaving it
        there would both block the cluster's unique index forever and tell an
        operator a playbook is still going.

        UNKNOWN rather than FAILED: the playbook may have finished perfectly.
        What is true is that nobody watched it end.
        """
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "UPDATE fleet_job_run SET state = %s, updated_at = now(),"
                    "       error = COALESCE(error,"
                    "         'tms-api restarted while this job was running, so"
                    "  its outcome was never observed. Check the nodes.')"
                    " WHERE state = %s", (UNKNOWN, RUNNING))
                return cursor.rowcount or 0
        except Exception as exc:  # noqa: BLE001
            log.warning("could not reconcile orphaned fleet jobs: %s", exc)
            return 0


def _run(row) -> Dict[str, Any]:
    parameters = row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}")
    return {"id": row[0], "cluster": row[1], "job": row[2], "state": row[3],
            "reason": row[4], "actor": row[5], "parameters": parameters,
            "started_at": row[7], "finished_at": row[8],
            "exit_code": row[9], "error": row[10],
            "is_terminal": row[3] in TERMINAL}
