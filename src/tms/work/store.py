"""Storage for the work board (items, comments, status changes).

Append-only where it matters: comments and status events are never rewritten,
for the same reason the audit log is not. An item itself is mutable - its
status and body change, which is the point of having it - but it is never
deleted. Something raised and then decided against becomes `dropped`, so the
reason someone raised it stays findable.

Python 3.9 compatible.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tms.work.items import DONE, DROPPED, next_request_key

log = logging.getLogger(__name__)

_COLUMNS = ("id", "key", "kind", "title", "status", "release", "blocked_by",
            "source_doc", "body", "created_by", "created_at", "updated_at")


class BoardUnavailable(Exception):
    """Storage is not reachable."""


class DuplicateKey(Exception):
    """That key is already on the board."""


def _row(values) -> Dict[str, Any]:
    return dict(zip(_COLUMNS, values))


class InMemoryBoardRepository:
    """For tests and the demo. Same interface, no durability."""

    def __init__(self):
        self.items: List[Dict[str, Any]] = []
        self.comments: Dict[Any, List[Dict[str, Any]]] = {}
        self.events: Dict[Any, List[Dict[str, Any]]] = {}
        self._next = 1

    def _now(self):
        return datetime.now(timezone.utc)

    def list_items(self, kind=None, status=None):
        rows = [dict(i) for i in self.items
                if (kind is None or i["kind"] == kind)
                and (status is None or i["status"] == status)]
        return sorted(rows, key=lambda i: (i["status"], i["key"]))

    def get(self, key):
        for item in self.items:
            if item["key"] == key:
                return dict(item,
                            comments=list(self.comments.get(item["id"], [])),
                            events=list(self.events.get(item["id"], [])))
        return None

    def create(self, key, kind, title, status, created_by, body="",
               release=None, blocked_by=None, source_doc=None):
        if any(i["key"] == key for i in self.items):
            raise DuplicateKey(key)
        now = self._now()
        item = {"id": self._next, "key": key, "kind": kind, "title": title,
                "status": status, "release": release, "blocked_by": blocked_by,
                "source_doc": source_doc, "body": body, "created_by": created_by,
                "created_at": now, "updated_at": now}
        self._next += 1
        self.items.append(item)
        self.comments[item["id"]] = []
        self.events[item["id"]] = []
        return dict(item)

    def update(self, key, actor, **fields):
        for item in self.items:
            if item["key"] != key:
                continue
            before = item["status"]
            for name in ("title", "status", "release", "blocked_by", "body"):
                if name in fields and fields[name] is not None:
                    item[name] = fields[name]
            item["updated_at"] = self._now()
            if item["status"] != before:
                self.events.setdefault(item["id"], []).append(
                    {"actor": actor, "from_status": before,
                     "to_status": item["status"], "occurred_at": self._now()})
            return dict(item)
        return None

    def add_comment(self, key, author, body):
        for item in self.items:
            if item["key"] == key:
                comment = {"author": author, "body": body,
                           "created_at": self._now()}
                self.comments.setdefault(item["id"], []).append(comment)
                return comment
        return None

    def next_key(self):
        return next_request_key([i["key"] for i in self.items])


class PostgresBoardRepository:
    """The real one."""

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "psycopg is required for PostgresBoardRepository") from exc
        self._psycopg = psycopg
        self._dsn = dsn
        self._connection = None

    def _cursor(self):
        """Connect on use, and reconnect after a failure.

        Deliberately unlike the other repositories, which connect at startup.
        Those are wired into features that must refuse to start without their
        storage; the board is a planning surface. Connecting eagerly would mean
        a database blip during boot removes the board from the console until
        somebody restarts tms-api - and a missing nav entry cannot be told
        apart from a feature that was never built.
        """
        try:
            if self._connection is None or self._connection.closed:
                self._connection = self._psycopg.connect(self._dsn, autocommit=True)
            return self._connection.cursor()
        except Exception as exc:  # noqa: BLE001
            self._connection = None
            raise BoardUnavailable(str(exc))

    def list_items(self, kind=None, status=None) -> List[Dict[str, Any]]:
        sql = "SELECT {} FROM work_item".format(", ".join(_COLUMNS))
        where, params = [], []
        if kind:
            where.append("kind = %s")
            params.append(kind)
        if status:
            where.append("status = %s")
            params.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY key"
        with self._cursor() as cursor:
            cursor.execute(sql, params)
            return [_row(r) for r in cursor.fetchall() or []]

    def get(self, key) -> Optional[Dict[str, Any]]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT {} FROM work_item WHERE key = %s".format(", ".join(_COLUMNS)),
                (key,))
            row = cursor.fetchone()
            if row is None:
                return None
            item = _row(row)
            cursor.execute(
                "SELECT author, body, created_at FROM work_item_comment"
                " WHERE item_id = %s ORDER BY id", (item["id"],))
            item["comments"] = [{"author": r[0], "body": r[1], "created_at": r[2]}
                                for r in cursor.fetchall() or []]
            cursor.execute(
                "SELECT actor, from_status, to_status, occurred_at"
                "  FROM work_item_event WHERE item_id = %s ORDER BY id",
                (item["id"],))
            item["events"] = [{"actor": r[0], "from_status": r[1],
                               "to_status": r[2], "occurred_at": r[3]}
                              for r in cursor.fetchall() or []]
        return item

    def create(self, key, kind, title, status, created_by, body="",
               release=None, blocked_by=None, source_doc=None) -> Dict[str, Any]:
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "INSERT INTO work_item"
                    " (key, kind, title, status, release, blocked_by, source_doc,"
                    "  body, created_by)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " RETURNING {}".format(", ".join(_COLUMNS)),
                    (key, kind, title, status, release, blocked_by, source_doc,
                     body, created_by))
                return _row(cursor.fetchone())
        except self._psycopg.errors.UniqueViolation:
            raise DuplicateKey(key)
        except BoardUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BoardUnavailable(str(exc))

    def update(self, key, actor, **fields) -> Optional[Dict[str, Any]]:
        sets, params = [], []
        for name in ("title", "status", "release", "blocked_by", "body"):
            if fields.get(name) is not None:
                sets.append("{} = %s".format(name))
                params.append(fields[name])
        if not sets:
            return self.get(key)

        with self._cursor() as cursor:
            cursor.execute("SELECT id, status FROM work_item WHERE key = %s", (key,))
            row = cursor.fetchone()
            if row is None:
                return None
            item_id, before = row

            cursor.execute(
                "UPDATE work_item SET {}, updated_at = now() WHERE key = %s".format(
                    ", ".join(sets)),
                params + [key])

            after = fields.get("status") or before
            if after != before:
                # A separate row rather than a column, so "who moved this and
                # when" survives the next move.
                cursor.execute(
                    "INSERT INTO work_item_event"
                    " (item_id, actor, from_status, to_status)"
                    " VALUES (%s, %s, %s, %s)", (item_id, actor, before, after))
        return self.get(key)

    def add_comment(self, key, author, body) -> Optional[Dict[str, Any]]:
        with self._cursor() as cursor:
            cursor.execute("SELECT id FROM work_item WHERE key = %s", (key,))
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                "INSERT INTO work_item_comment (item_id, author, body)"
                " VALUES (%s, %s, %s) RETURNING created_at", (row[0], author, body))
            return {"author": author, "body": body,
                    "created_at": cursor.fetchone()[0]}

    def next_key(self) -> str:
        with self._cursor() as cursor:
            cursor.execute("SELECT key FROM work_item WHERE key LIKE 'REQ-%'")
            return next_request_key([r[0] for r in cursor.fetchall() or []])

    def close(self) -> None:
        """For short-lived callers - the export command, tests.

        tms-api holds its connection for the life of the process and never
        calls this. A command that exits without it leaves the connection for
        the server to reap, which works and still warns on the way out.
        """
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None
