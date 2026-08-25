"""Storage for query sets an administrator edits in the console (FR-BM-06).

Deliberately *not* append-only, unlike `store.py` next door. A query set is
configuration - it says what to measure - and the thing this replaced was a
block of YAML that anyone could edit and redeploy. The measurements stay
evidence: `benchmark_result` holds the numbers and `benchmark_run.queries`
holds the statement that produced each one, both copied by value. Deleting a
query removes it from future runs and takes nothing away from past ones.

The read interface is `get()` / `values()` so it drops straight into the seat
the config-built dict used to occupy in `BenchmarkService`.

Python 3.9 compatible.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from tms.bench.queryset import BenchmarkQuery, QuerySet

log = logging.getLogger(__name__)


class QuerySetStoreUnavailable(Exception):
    """Storage is not reachable.

    Blocking on the write path, tolerated on the read path: a console that
    cannot list sets should say so, but it must not take the rest of the
    benchmark page down with it.
    """


class UnknownSet(Exception):
    """No set by that key."""


class DuplicateName(Exception):
    """That key or query name is already taken."""


def _sorted(queries: List[BenchmarkQuery]) -> List[BenchmarkQuery]:
    """Position first, then name. Total either way, so a run is repeatable."""
    return sorted(queries, key=lambda q: (getattr(q, "position", 0), q.name))


class InMemoryQuerySetRepository:
    """For tests and the demo. Same interface, no durability."""

    def __init__(self, sets: Optional[Dict[str, QuerySet]] = None) -> None:
        self._sets: Dict[str, QuerySet] = dict(sets or {})

    def get(self, key: str) -> Optional[QuerySet]:
        return self._sets.get(key)

    def values(self) -> List[QuerySet]:
        return [self._sets[k] for k in sorted(self._sets)]

    def save_set(self, key, title, description, actor) -> QuerySet:
        found = self._sets.get(key)
        if found is None:
            found = QuerySet(key=key, title=title, description=description,
                             queries=[])
            self._sets[key] = found
        else:
            found.title = title or key
            found.description = description
        return found

    def delete_set(self, key) -> bool:
        return self._sets.pop(key, None) is not None

    def save_query(self, set_key, name, title, statement, position, actor,
                   original_name=None) -> BenchmarkQuery:
        found = self._sets.get(set_key)
        if found is None:
            raise UnknownSet(set_key)
        existing = {q.name: q for q in found.queries}
        if name in existing and name != original_name:
            raise DuplicateName(name)
        query = existing.pop(original_name, None) if original_name else None
        if query is None:
            query = BenchmarkQuery(name=name, sql=statement, title=title)
            found.queries.append(query)
        else:
            query.name, query.sql, query.title = name, statement, title
        query.position = int(position)
        found.queries = _sorted(found.queries)
        return query

    def delete_query(self, set_key, name) -> bool:
        found = self._sets.get(set_key)
        if found is None:
            raise UnknownSet(set_key)
        remaining = [q for q in found.queries if q.name != name]
        if len(remaining) == len(found.queries):
            return False
        found.queries = remaining
        return True


class PostgresQuerySetRepository:
    """The real one."""

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "psycopg is required for PostgresQuerySetRepository") from exc
        self._psycopg = psycopg
        self._dsn = dsn
        self._connection = psycopg.connect(dsn, autocommit=True)

    def _cursor(self):
        try:
            return self._connection.cursor()
        except Exception as exc:  # noqa: BLE001
            raise QuerySetStoreUnavailable(str(exc))

    # --------------------------------------------------------------- read

    def get(self, key: str) -> Optional[QuerySet]:
        for found in self._load(key=key):
            return found
        return None

    def values(self) -> List[QuerySet]:
        return self._load()

    def _load(self, key: Optional[str] = None) -> List[QuerySet]:
        sql = ("SELECT s.key, s.title, s.description,"
               "       q.name, q.title, q.statement, q.position"
               "  FROM benchmark_query_set s"
               "  LEFT JOIN benchmark_query q ON q.set_id = s.id")
        params: List[Any] = []
        if key is not None:
            sql += " WHERE s.key = %s"
            params.append(key)
        sql += " ORDER BY s.key, q.position, q.name"
        try:
            with self._cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall() or []
        except QuerySetStoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise QuerySetStoreUnavailable(str(exc))

        sets: Dict[str, QuerySet] = {}
        for set_key, set_title, description, name, title, statement, position in rows:
            found = sets.get(set_key)
            if found is None:
                found = QuerySet(key=set_key, title=set_title,
                                 description=description or "", queries=[])
                sets[set_key] = found
            # LEFT JOIN: a set with no queries yet arrives as one all-NULL row.
            # It is a real set - somebody just created it - and hiding it would
            # leave them on a page that says their set does not exist.
            if name is None:
                continue
            found.queries.append(BenchmarkQuery(
                name=name, sql=statement, title=title or "",
                position=int(position or 0)))
        return [sets[k] for k in sorted(sets)]

    # -------------------------------------------------------------- write

    def save_set(self, key, title, description, actor) -> QuerySet:
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "INSERT INTO benchmark_query_set"
                    " (key, title, description, created_by, updated_by)"
                    " VALUES (%s, %s, %s, %s, %s)"
                    " ON CONFLICT (key) DO UPDATE"
                    "    SET title = EXCLUDED.title,"
                    "        description = EXCLUDED.description,"
                    "        updated_at = now(), updated_by = EXCLUDED.updated_by",
                    (key, title or "", description or "", actor, actor))
        except QuerySetStoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise QuerySetStoreUnavailable(str(exc))
        return self.get(key)

    def delete_set(self, key) -> bool:
        # The queries go with it: 018 declares ON DELETE CASCADE. Past runs do
        # not - `benchmark_run.query_set` is a plain column, never a reference.
        try:
            with self._cursor() as cursor:
                cursor.execute("DELETE FROM benchmark_query_set WHERE key = %s",
                               (key,))
                return bool(cursor.rowcount)
        except QuerySetStoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise QuerySetStoreUnavailable(str(exc))

    def save_query(self, set_key, name, title, statement, position, actor,
                   original_name=None) -> BenchmarkQuery:
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM benchmark_query_set WHERE key = %s", (set_key,))
                row = cursor.fetchone()
                if row is None:
                    raise UnknownSet(set_key)
                set_id = row[0]

                if original_name:
                    cursor.execute(
                        "UPDATE benchmark_query"
                        "   SET name = %s, title = %s, statement = %s,"
                        "       position = %s, updated_at = now(), updated_by = %s"
                        " WHERE set_id = %s AND name = %s",
                        (name, title or "", statement, int(position), actor,
                         set_id, original_name))
                    if cursor.rowcount:
                        return BenchmarkQuery(name=name, sql=statement, title=title)
                    # The row is gone - somebody deleted it while this form was
                    # open. Fall through and insert it rather than reporting a
                    # failure for an edit whose end state is reachable anyway.
                cursor.execute(
                    "INSERT INTO benchmark_query"
                    " (set_id, name, title, statement, position,"
                    "  created_by, updated_by)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (set_id, name, title or "", statement, int(position),
                     actor, actor))
        except self._psycopg.errors.UniqueViolation:
            raise DuplicateName(name)
        except (QuerySetStoreUnavailable, UnknownSet, DuplicateName):
            raise
        except Exception as exc:  # noqa: BLE001
            raise QuerySetStoreUnavailable(str(exc))
        return BenchmarkQuery(name=name, sql=statement, title=title)

    def delete_query(self, set_key, name) -> bool:
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "DELETE FROM benchmark_query"
                    " WHERE name = %s AND set_id ="
                    "   (SELECT id FROM benchmark_query_set WHERE key = %s)",
                    (name, set_key))
                return bool(cursor.rowcount)
        except QuerySetStoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise QuerySetStoreUnavailable(str(exc))

