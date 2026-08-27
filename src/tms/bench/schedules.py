"""Benchmark schedules — when to measure, without anybody pressing anything.

D-015 removed the "cluster must be out of rotation" gate, and the reason it
gave was that what is actually needed is the performance of a *serving*
cluster, measured periodically, so the trend means something. The gate went;
the periodic half was never built, so every point on the chart still needed a
person. This is that half (D-017).

Three properties are not negotiable:

* **A scheduled run carries a who and a why.** Absolute rule 3 does not have an
  exception for "nobody was there". `created_by` is the actor and the
  schedule's `reason` rides onto every run it starts.
* **Claiming is atomic.** `UPDATE ... WHERE next_run_at <= now() RETURNING` -
  two processes ticking the same second cannot both take the same schedule.
  There is one API host today; this costs one query and stops that from being
  an assumption.
* **A schedule that keeps failing stops itself.** A broken query set running
  every night forever is load with no reader.

⛔ No cron expression. `interval_minutes` + `next_run_at` says "every day at
03:00", "every six hours" and "weekly" without a parser or a timezone
argument. What it cannot say is "weekdays only" - when that is asked for it is
a new column, not a cron string bolted on.

Python 3.9 compatible.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: Below this a "schedule" is a load generator. The floor is deliberate rather
#: than advisory: a benchmark takes cluster capacity, and one every minute on a
#: serving cluster is the incident D-015 accepted the risk of, not the trend it
#: accepted it for.
MIN_INTERVAL_MINUTES = 15

#: Nothing needs to measure less often than this, and a schedule that fires
#: once a year is a schedule nobody will remember exists.
MAX_INTERVAL_MINUTES = 60 * 24 * 90

#: Consecutive failures before TMS switches a schedule off for the operator.
#: Three rather than one: a single failure is often the cluster, not the set.
FAILURE_LIMIT = 3


class ScheduleStoreUnavailable(Exception):
    """The schedule table could not be reached."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate(name: str, interval_minutes: Any, repetitions: Any,
             clusters: List[str], reason: Optional[str]) -> Dict[str, Any]:
    """Refuse a schedule that cannot mean anything. Raises ValueError."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A schedule needs a name.")
    if len(name) > 200:
        raise ValueError("That name is too long (200 characters).")

    if not (reason or "").strip():
        raise ValueError(
            "A reason is required. It is recorded on every run this schedule "
            "starts, because nobody will be there to explain them.")

    try:
        interval = int(interval_minutes)
    except (TypeError, ValueError):
        raise ValueError("The interval must be a number of minutes.")
    if interval < MIN_INTERVAL_MINUTES:
        raise ValueError(
            "The shortest interval is {} minutes. A benchmark takes cluster "
            "capacity; more often than that is a load generator, not a "
            "measurement.".format(MIN_INTERVAL_MINUTES))
    if interval > MAX_INTERVAL_MINUTES:
        raise ValueError("The longest interval is 90 days.")

    try:
        reps = int(repetitions)
    except (TypeError, ValueError):
        raise ValueError("Repetitions must be a number.")
    if not 1 <= reps <= 50:
        raise ValueError("Repetitions must be between 1 and 50.")

    chosen = [c for c in (clusters or []) if c]
    if not chosen:
        raise ValueError("Pick at least one cluster.")

    return {"name": name, "interval_minutes": interval, "repetitions": reps,
            "clusters": chosen, "reason": reason.strip()}


def advance(next_run_at: datetime, interval_minutes: int,
            now: Optional[datetime] = None) -> datetime:
    """The next firing after this one.

    ⛔ Steps forward from the scheduled time, not from now, so a daily 03:00
    schedule stays at 03:00 instead of drifting later every day by however long
    the run took.

    ⛔ Skips past whole missed intervals rather than firing once per missed
    one. TMS being down for a weekend must not produce a burst of catch-up
    benchmarks against a live cluster on Monday morning.
    """
    now = now or utcnow()
    step = timedelta(minutes=interval_minutes)
    nxt = next_run_at + step
    if nxt <= now:
        missed = int((now - next_run_at).total_seconds() // step.total_seconds())
        nxt = next_run_at + step * (missed + 1)
    return nxt


class InMemoryScheduleRepository:
    """Test double with the same guarantees the schema provides."""

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []
        self._next_id = 1

    def create(self, **fields) -> Dict[str, Any]:
        if any(r["name"] == fields["name"] for r in self.rows):
            raise ValueError("A schedule called {!r} already exists.".format(
                fields["name"]))
        row = dict(fields, id=self._next_id, enabled=True, paused_reason=None,
                   consecutive_failures=0, last_run_at=None, last_outcome=None,
                   created_at=utcnow())
        self._next_id += 1
        self.rows.append(row)
        return dict(row)

    def list(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in sorted(self.rows, key=lambda r: r["name"])]

    def get(self, schedule_id) -> Optional[Dict[str, Any]]:
        for row in self.rows:
            if str(row["id"]) == str(schedule_id):
                return dict(row)
        return None

    def update(self, schedule_id, **changes) -> Optional[Dict[str, Any]]:
        for row in self.rows:
            if str(row["id"]) == str(schedule_id):
                row.update(changes)
                return dict(row)
        return None

    def delete(self, schedule_id) -> bool:
        before = len(self.rows)
        self.rows = [r for r in self.rows if str(r["id"]) != str(schedule_id)]
        return len(self.rows) != before

    def claim_due(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Take every schedule whose time has come, moving it forward first."""
        now = now or utcnow()
        claimed = []
        for row in self.rows:
            if not row["enabled"] or row["next_run_at"] > now:
                continue
            row["next_run_at"] = advance(row["next_run_at"],
                                         row["interval_minutes"], now)
            claimed.append(dict(row))
        return claimed


class PostgresScheduleRepository:
    """The real one. Same methods, one connection per call."""

    def __init__(self, dsn: str) -> None:
        import psycopg  # noqa: F401 - fail here rather than at first use

        self._dsn = dsn

    def _cursor(self):
        import psycopg
        from psycopg.rows import dict_row

        try:
            return psycopg.connect(self._dsn, row_factory=dict_row, autocommit=True)
        except Exception as exc:  # noqa: BLE001
            raise ScheduleStoreUnavailable(str(exc))

    def _run(self, sql: str, params=(), fetch: str = "all"):
        try:
            with self._cursor() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    if fetch == "none":
                        return None
                    if fetch == "one":
                        return _row(cursor.fetchone())
                    return [_row(r) for r in cursor.fetchall()]
        except ScheduleStoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ScheduleStoreUnavailable(str(exc))

    def create(self, **fields) -> Dict[str, Any]:
        return self._run(
            """
            INSERT INTO benchmark_schedule
                (name, query_set, clusters, repetitions, label, reason,
                 interval_minutes, next_run_at, created_by)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (fields["name"], fields["query_set"], json.dumps(fields["clusters"]),
             fields["repetitions"], fields.get("label"), fields["reason"],
             fields["interval_minutes"], fields["next_run_at"],
             fields["created_by"]),
            fetch="one")

    def list(self) -> List[Dict[str, Any]]:
        return self._run("SELECT * FROM benchmark_schedule ORDER BY name")

    def get(self, schedule_id) -> Optional[Dict[str, Any]]:
        return self._run("SELECT * FROM benchmark_schedule WHERE id = %s",
                         (schedule_id,), fetch="one")

    def update(self, schedule_id, **changes) -> Optional[Dict[str, Any]]:
        if not changes:
            return self.get(schedule_id)
        columns, values = [], []
        for column, value in changes.items():
            columns.append("{} = %s{}".format(
                column, "::jsonb" if column == "clusters" else ""))
            values.append(json.dumps(value) if column == "clusters" else value)
        values.append(schedule_id)
        return self._run(
            "UPDATE benchmark_schedule SET {} WHERE id = %s RETURNING *".format(
                ", ".join(columns)), tuple(values), fetch="one")

    def delete(self, schedule_id) -> bool:
        return bool(self._run(
            "DELETE FROM benchmark_schedule WHERE id = %s RETURNING id",
            (schedule_id,), fetch="one"))

    def claim_due(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """⛔ One statement. The row is moved forward in the same UPDATE that
        selects it, so two processes ticking the same second cannot both take
        it - and a crash between claiming and running loses one measurement
        rather than starting an unbounded number of them.
        """
        return self._run(
            """
            UPDATE benchmark_schedule AS s
               SET next_run_at = s.next_run_at
                   + (s.interval_minutes * INTERVAL '1 minute')
                   * (1 + FLOOR(EXTRACT(EPOCH FROM (now() - s.next_run_at))
                                / (s.interval_minutes * 60)))
             WHERE s.enabled
               AND s.next_run_at <= now()
            RETURNING *
            """)


def _row(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out = dict(row)
    clusters = out.get("clusters")
    if isinstance(clusters, str):
        out["clusters"] = json.loads(clusters)
    return out
