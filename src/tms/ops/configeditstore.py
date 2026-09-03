"""Where config.properties changes and their deployments live.

Two tables with different natures, and the GRANTs in migration 028 say so: a
change is **configuration** and may be deleted, a deployment is **evidence**
and may not. The row for the change that stopped a cluster booting is the one
somebody will need most.

Python 3.9 compatible.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class ConfigStoreUnavailable(Exception):
    """The config change tables could not be reached."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryConfigChangeRepository:
    """Test double with the same guarantees the schema provides."""

    def __init__(self) -> None:
        self.changes: List[Dict[str, Any]] = []
        self.deployments: List[Dict[str, Any]] = []
        self._next = 1
        self._next_deploy = 1

    def create(self, title, target_role, entries, notes, actor):
        row = {"id": self._next, "title": title, "target_role": target_role,
               "entries": list(entries), "notes": notes,
               "verified_on": None, "verified_at": None,
               "created_by": actor, "created_at": utcnow(),
               "updated_by": None, "updated_at": None}
        self._next += 1
        self.changes.append(row)
        return dict(row)

    def list(self):
        return [dict(c) for c in sorted(self.changes,
                                        key=lambda c: c["created_at"], reverse=True)]

    def get(self, change_id):
        for row in self.changes:
            if str(row["id"]) == str(change_id):
                return dict(row)
        return None

    def update(self, change_id, **changes):
        for row in self.changes:
            if str(row["id"]) == str(change_id):
                row.update(changes)
                return dict(row)
        return None

    def delete(self, change_id):
        before = len(self.changes)
        self.changes = [c for c in self.changes if str(c["id"]) != str(change_id)]
        return len(self.changes) != before

    def start_deployment(self, change_id, title, cluster, target_role, entries,
                         reason, actor):
        row = {"id": self._next_deploy, "change_id": change_id, "title": title,
               "cluster": cluster, "target_role": target_role,
               "entries": list(entries), "reason": reason, "actor": actor,
               "state": "RUNNING", "detail": None, "log": None,
               "started_at": utcnow(), "finished_at": None}
        self._next_deploy += 1
        self.deployments.append(row)
        return dict(row)

    def finish_deployment(self, deployment_id, state, detail=None, log=None):
        for row in self.deployments:
            if str(row["id"]) == str(deployment_id):
                row.update(state=state, detail=detail, log=log,
                           finished_at=utcnow())
                return dict(row)
        return None

    def recent_deployments(self, limit=25):
        return [dict(d) for d in sorted(self.deployments,
                                        key=lambda d: d["started_at"],
                                        reverse=True)][:limit]


class PostgresConfigChangeRepository:
    """The real one. Same methods, one connection per call."""

    def __init__(self, dsn: str) -> None:
        import psycopg  # noqa: F401 - fail here rather than at first use

        self._dsn = dsn

    def _run(self, sql, params=(), fetch="all"):
        import psycopg
        from psycopg.rows import dict_row

        try:
            with psycopg.connect(self._dsn, row_factory=dict_row,
                                 autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    if fetch == "none":
                        return None
                    if fetch == "one":
                        row = cursor.fetchone()
                        return dict(row) if row else None
                    return [dict(r) for r in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            raise ConfigStoreUnavailable(str(exc))

    def create(self, title, target_role, entries, notes, actor):
        return self._run(
            "INSERT INTO config_change"
            " (title, target_role, entries, notes, created_by)"
            " VALUES (%s, %s, %s::jsonb, %s, %s) RETURNING *",
            (title, target_role, json.dumps(entries), notes, actor), fetch="one")

    def list(self):
        return self._run("SELECT * FROM config_change ORDER BY created_at DESC")

    def get(self, change_id):
        return self._run("SELECT * FROM config_change WHERE id = %s",
                         (change_id,), fetch="one")

    def update(self, change_id, **changes):
        if not changes:
            return self.get(change_id)
        columns, values = [], []
        for column, value in changes.items():
            columns.append("{} = %s{}".format(
                column, "::jsonb" if column == "entries" else ""))
            values.append(json.dumps(value) if column == "entries" else value)
        values.append(change_id)
        return self._run(
            "UPDATE config_change SET {} WHERE id = %s RETURNING *".format(
                ", ".join(columns)), tuple(values), fetch="one")

    def delete(self, change_id):
        return bool(self._run(
            "DELETE FROM config_change WHERE id = %s RETURNING id",
            (change_id,), fetch="one"))

    def start_deployment(self, change_id, title, cluster, target_role, entries,
                         reason, actor):
        return self._run(
            "INSERT INTO config_deployment"
            " (change_id, title, cluster, target_role, entries, reason, actor)"
            " VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s) RETURNING *",
            (change_id, title, cluster, target_role, json.dumps(entries),
             reason, actor), fetch="one")

    def finish_deployment(self, deployment_id, state, detail=None, log=None):
        return self._run(
            "UPDATE config_deployment"
            " SET state = %s, detail = %s, log = %s, finished_at = now()"
            " WHERE id = %s RETURNING *",
            (state, detail, log, deployment_id), fetch="one")

    def recent_deployments(self, limit=25):
        return self._run(
            "SELECT * FROM config_deployment ORDER BY started_at DESC LIMIT %s",
            (limit,))
