"""Storage for restart sequences (FR-CO-02).

A sequence is live state that outlives the process running it. If tms-api is
restarted while a cluster is deactivated and the sequence is only in memory,
that cluster stays out of rotation with nothing pointing at it - and every
other cluster is green, so the console looks healthy. Persisting it is what
makes "there is a restart in progress" survivable.

Two storage rules, both enforced by the schema rather than here:

* one active sequence per cluster (partial unique index),
* events are append-only, like the audit log.

There is no `delete`. Abandoning a sequence is a state (`ABORTED`), reached by
restoring traffic - not by removing the row.

Python 3.9 compatible.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from tms.ops.sequence import TERMINAL, RestartSequence

log = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SequenceUnavailable(Exception):
    """Storage is not reachable.

    Callers must treat this as blocking: starting a restart TMS cannot record
    would produce exactly the untracked deactivation this table exists to
    prevent.
    """


class ActiveSequenceExists(Exception):
    """A restart of this cluster is already in flight."""


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


class StoredSequence:
    """A sequence plus what storage knows about it.

    `started_at` and `finished_at` come from storage rather than the sequence,
    which deliberately holds no clock of its own. The history list would give
    an approximation, but "when was this cluster restarted" is a question asked
    of a record - it should be the recorded time, not one inferred from it.
    """

    __slots__ = ("id", "sequence", "_persisted_events", "started_at", "finished_at")

    def __init__(self, sequence_id: Any, sequence: RestartSequence,
                 persisted_events: int = 0, started_at: Optional[str] = None,
                 finished_at: Optional[str] = None) -> None:
        self.id = sequence_id
        self.sequence = sequence
        self._persisted_events = persisted_events
        self.started_at = started_at
        self.finished_at = finished_at

    def pending_events(self) -> List[Dict[str, Any]]:
        return self.sequence.history[self._persisted_events:]

    def mark_persisted(self) -> None:
        self._persisted_events = len(self.sequence.history)

    def as_dict(self) -> Dict[str, Any]:
        payload = self.sequence.as_dict()
        payload["id"] = self.id
        payload["started_at"] = self.started_at
        payload["finished_at"] = self.finished_at
        return payload


def _rebuild(row: Dict[str, Any], events: List[Dict[str, Any]]) -> RestartSequence:
    sequence = RestartSequence(
        cluster=row["cluster"], reason=row["reason"], actor=row["actor"])
    sequence.state = row["state"]
    sequence.running_queries = row.get("running_queries")
    sequence.health_state = row.get("health_state")
    sequence.history = list(events)
    return sequence


class InMemorySequenceRepository:
    """Test double with the same guarantees the schema provides."""

    def __init__(self) -> None:
        self._rows: Dict[Any, Dict[str, Any]] = {}
        self._events: Dict[Any, List[Dict[str, Any]]] = {}
        self._next_id = 1

    def create(self, sequence: RestartSequence,
               roles: Optional[List[str]] = None) -> StoredSequence:
        if self.active_for(sequence.cluster) is not None:
            raise ActiveSequenceExists(
                "a restart of {} is already in progress".format(sequence.cluster))
        sequence_id = self._next_id
        self._next_id += 1
        self._rows[sequence_id] = {
            "id": sequence_id, "cluster": sequence.cluster, "state": sequence.state,
            "reason": sequence.reason, "actor": sequence.actor,
            "running_queries": sequence.running_queries,
            "health_state": sequence.health_state,
            "actor_roles": list(roles or []),
            "started_at": _utcnow_iso(), "finished_at": None,
        }
        self._events[sequence_id] = []
        stored = StoredSequence(sequence_id, sequence)
        self.save(stored)
        return stored

    def save(self, stored: StoredSequence,
             force_reason: Optional[str] = None) -> None:
        row = self._rows[stored.id]
        if force_reason:
            row["force_reason"] = force_reason
        row["state"] = stored.sequence.state
        row["running_queries"] = stored.sequence.running_queries
        row["health_state"] = stored.sequence.health_state
        if stored.sequence.state in TERMINAL and row["finished_at"] is None:
            row["finished_at"] = _utcnow_iso()
        self._events[stored.id].extend(stored.pending_events())
        stored.mark_persisted()

    def load(self, sequence_id: Any) -> Optional[StoredSequence]:
        row = self._rows.get(sequence_id)
        if row is None:
            return None
        events = self._events.get(sequence_id, [])
        return StoredSequence(sequence_id, _rebuild(row, events), len(events),
                              started_at=row["started_at"],
                              finished_at=row["finished_at"])

    def active_for(self, cluster: str) -> Optional[StoredSequence]:
        for sequence_id, row in self._rows.items():
            if row["cluster"] == cluster and row["state"] not in TERMINAL:
                return self.load(sequence_id)
        return None

    def all_active(self) -> List[StoredSequence]:
        return [self.load(i) for i, row in sorted(self._rows.items())
                if row["state"] not in TERMINAL]

    def recent(self, limit: int = 20) -> List[StoredSequence]:
        ids = sorted(self._rows, reverse=True)[:limit]
        return [self.load(i) for i in ids]


_INSERT_SEQUENCE = """
INSERT INTO restart_sequence (cluster, state, reason, actor, actor_roles,
                              running_queries, health_state)
VALUES (%s, %s, %s, %s, %s, %s, %s)
RETURNING id
"""

_UPDATE_SEQUENCE = """
UPDATE restart_sequence
   SET state = %s, running_queries = %s, health_state = %s,
       force_reason = COALESCE(%s, force_reason),
       updated_at = now(),
       finished_at = CASE WHEN %s THEN COALESCE(finished_at, now()) ELSE NULL END
 WHERE id = %s
"""

_INSERT_EVENT = """
INSERT INTO restart_sequence_event (sequence_id, occurred_at, state, level, message)
VALUES (%s, COALESCE(%s::timestamptz, now()), %s, %s, %s)
"""

_SELECT_SEQUENCE = """
SELECT id, cluster, state, reason, actor, running_queries, health_state,
       started_at, finished_at
  FROM restart_sequence
"""

_SELECT_EVENTS = """
SELECT occurred_at, state, level, message
  FROM restart_sequence_event
 WHERE sequence_id = %s
 ORDER BY occurred_at, id
"""


class PostgresSequenceRepository:
    """Backed by `restart_sequence` / `restart_sequence_event`.

    No DELETE and no UPDATE of events appears in this file, and 005 withholds
    both privileges from the application role as a second line of defence.
    """

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "psycopg is required for PostgresSequenceRepository") from exc
        self._psycopg = psycopg
        self._dsn = dsn
        self._connection = psycopg.connect(dsn, autocommit=True)

    # ----------------------------------------------------------------- util

    def _cursor(self):
        try:
            if self._connection.closed:
                self._connection = self._psycopg.connect(self._dsn, autocommit=True)
            return self._connection.cursor()
        except Exception as exc:  # noqa: BLE001
            raise SequenceUnavailable(str(exc)) from exc

    def _events_for(self, cursor, sequence_id) -> List[Dict[str, Any]]:
        cursor.execute(_SELECT_EVENTS, (sequence_id,))
        return [
            {"at": at.isoformat(), "state": state, "level": level, "message": message}
            for at, state, level, message in cursor.fetchall()
        ]

    def _hydrate(self, cursor, row: Tuple) -> StoredSequence:
        (sequence_id, cluster, state, reason, actor, running, health,
         started_at, finished_at) = row
        events = self._events_for(cursor, sequence_id)
        sequence = _rebuild(
            {"cluster": cluster, "state": state, "reason": reason, "actor": actor,
             "running_queries": running, "health_state": health},
            events)
        return StoredSequence(sequence_id, sequence, len(events),
                              started_at=_iso(started_at), finished_at=_iso(finished_at))

    # ------------------------------------------------------------ operations

    def create(self, sequence: RestartSequence,
               roles: Optional[List[str]] = None) -> StoredSequence:
        try:
            with self._cursor() as cursor:
                cursor.execute(_INSERT_SEQUENCE, (
                    sequence.cluster, sequence.state, sequence.reason,
                    sequence.actor, roles or [],
                    sequence.running_queries, sequence.health_state))
                sequence_id = cursor.fetchone()[0]
        except SequenceUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            # The partial unique index is what makes concurrent restarts of one
            # cluster impossible; surface it as the domain error it is.
            if "restart_sequence_one_active_per_cluster" in str(exc):
                raise ActiveSequenceExists(
                    "a restart of {} is already in progress".format(sequence.cluster))
            raise SequenceUnavailable(str(exc)) from exc

        stored = StoredSequence(sequence_id, sequence)
        self.save(stored)
        return stored

    def save(self, stored: StoredSequence, force_reason: Optional[str] = None) -> None:
        sequence = stored.sequence
        pending = stored.pending_events()
        try:
            with self._cursor() as cursor:
                cursor.execute(_UPDATE_SEQUENCE, (
                    sequence.state, sequence.running_queries, sequence.health_state,
                    force_reason, sequence.state in TERMINAL, stored.id))
                for event in pending:
                    cursor.execute(_INSERT_EVENT, (
                        stored.id, event.get("at"), event.get("state"),
                        event.get("level", "info"), event.get("message", "")))
        except SequenceUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SequenceUnavailable(str(exc)) from exc
        stored.mark_persisted()

    def load(self, sequence_id: Any) -> Optional[StoredSequence]:
        with self._cursor() as cursor:
            cursor.execute(_SELECT_SEQUENCE + " WHERE id = %s", (sequence_id,))
            row = cursor.fetchone()
            return self._hydrate(cursor, row) if row else None

    def active_for(self, cluster: str) -> Optional[StoredSequence]:
        with self._cursor() as cursor:
            cursor.execute(
                _SELECT_SEQUENCE
                + " WHERE cluster = %s AND state NOT IN ('COMPLETED','ABORTED')",
                (cluster,))
            row = cursor.fetchone()
            return self._hydrate(cursor, row) if row else None

    def all_active(self) -> List[StoredSequence]:
        with self._cursor() as cursor:
            cursor.execute(
                _SELECT_SEQUENCE
                + " WHERE state NOT IN ('COMPLETED','ABORTED') ORDER BY started_at")
            rows = cursor.fetchall()
            return [self._hydrate(cursor, row) for row in rows]

    def recent(self, limit: int = 20) -> List[StoredSequence]:
        with self._cursor() as cursor:
            cursor.execute(
                _SELECT_SEQUENCE + " ORDER BY started_at DESC LIMIT %s",
                (max(1, min(int(limit), 200)),))
            rows = cursor.fetchall()
            return [self._hydrate(cursor, row) for row in rows]

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001
            pass
