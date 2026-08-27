"""Where catalog drafts and their deployments live.

Two tables with different natures, and the GRANTs in migration 024 say so:
a draft is **configuration** and may be deleted, a deployment is **evidence**
and may not. The row for a catalog that stopped a cluster booting is the one
somebody will need most.

⛔ A deployment stores the properties **by value**. The draft can be edited
afterwards, and "what did we actually put on prod-a last Tuesday" has to stay
answerable - the same reason `benchmark_run.queries` exists (D-014).

Python 3.9 compatible.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class CatalogStoreUnavailable(Exception):
    """The catalog tables could not be reached."""


class DuplicateCatalog(Exception):
    """A catalog with that name already exists."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryCatalogRepository:
    """Test double with the same guarantees the schema provides."""

    def __init__(self) -> None:
        self.drafts: List[Dict[str, Any]] = []
        self.deployments: List[Dict[str, Any]] = []
        self._next = 1
        self._next_deploy = 1

    # ------------------------------------------------------------- drafts

    def create(self, name, connector, properties, notes, actor) -> Dict[str, Any]:
        if any(d["name"] == name for d in self.drafts):
            raise DuplicateCatalog(name)
        row = {"id": self._next, "name": name, "connector": connector,
               "properties": dict(properties), "notes": notes,
               "verified_on": None, "verified_at": None,
               "created_by": actor, "created_at": utcnow(),
               "updated_by": None, "updated_at": None}
        self._next += 1
        self.drafts.append(row)
        return dict(row)

    def list(self) -> List[Dict[str, Any]]:
        return [dict(d) for d in sorted(self.drafts, key=lambda d: d["name"])]

    def get(self, catalog_id) -> Optional[Dict[str, Any]]:
        for row in self.drafts:
            if str(row["id"]) == str(catalog_id):
                return dict(row)
        return None

    def update(self, catalog_id, **changes) -> Optional[Dict[str, Any]]:
        for row in self.drafts:
            if str(row["id"]) == str(catalog_id):
                row.update(changes)
                return dict(row)
        return None

    def delete(self, catalog_id) -> bool:
        before = len(self.drafts)
        self.drafts = [d for d in self.drafts if str(d["id"]) != str(catalog_id)]
        return len(self.drafts) != before

    # -------------------------------------------------------- deployments

    def start_deployment(self, catalog_id, name, cluster, action, connector,
                         properties, reason, actor) -> Dict[str, Any]:
        row = {"id": self._next_deploy, "catalog_id": catalog_id,
               "catalog_name": name, "cluster": cluster, "action": action,
               "connector": connector, "properties": dict(properties),
               "state": "RUNNING", "detail": None, "reason": reason,
               "actor": actor, "started_at": utcnow(), "finished_at": None}
        self._next_deploy += 1
        self.deployments.append(row)
        return dict(row)

    def finish_deployment(self, deployment_id, state, detail=None) -> None:
        for row in self.deployments:
            if str(row["id"]) == str(deployment_id):
                row.update(state=state, detail=detail, finished_at=utcnow())

    def recent_deployments(self, limit=25, catalog_id=None) -> List[Dict[str, Any]]:
        rows = self.deployments
        if catalog_id is not None:
            rows = [r for r in rows if str(r["catalog_id"]) == str(catalog_id)]
        return [dict(r) for r in sorted(rows, key=lambda r: r["started_at"],
                                        reverse=True)][:limit]


class PostgresCatalogRepository:
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
                        return _row(cursor.fetchone())
                    return [_row(r) for r in cursor.fetchall()]
        except psycopg.errors.UniqueViolation:
            raise DuplicateCatalog("a catalog with that name already exists")
        except Exception as exc:  # noqa: BLE001
            raise CatalogStoreUnavailable(str(exc))

    def create(self, name, connector, properties, notes, actor):
        return self._run(
            "INSERT INTO catalog_definition"
            " (name, connector, properties, notes, created_by)"
            " VALUES (%s, %s, %s::jsonb, %s, %s) RETURNING *",
            (name, connector, json.dumps(properties), notes, actor), fetch="one")

    def list(self):
        return self._run("SELECT * FROM catalog_definition ORDER BY name")

    def get(self, catalog_id):
        return self._run("SELECT * FROM catalog_definition WHERE id = %s",
                         (catalog_id,), fetch="one")

    def update(self, catalog_id, **changes):
        if not changes:
            return self.get(catalog_id)
        columns, values = [], []
        for column, value in changes.items():
            columns.append("{} = %s{}".format(
                column, "::jsonb" if column == "properties" else ""))
            values.append(json.dumps(value) if column == "properties" else value)
        values.append(catalog_id)
        return self._run(
            "UPDATE catalog_definition SET {} WHERE id = %s RETURNING *".format(
                ", ".join(columns)), tuple(values), fetch="one")

    def delete(self, catalog_id):
        return bool(self._run(
            "DELETE FROM catalog_definition WHERE id = %s RETURNING id",
            (catalog_id,), fetch="one"))

    def start_deployment(self, catalog_id, name, cluster, action, connector,
                         properties, reason, actor):
        return self._run(
            "INSERT INTO catalog_deployment"
            " (catalog_id, catalog_name, cluster, action, connector,"
            "  properties, state, reason, actor)"
            " VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'RUNNING', %s, %s)"
            " RETURNING *",
            (catalog_id, name, cluster, action, connector,
             json.dumps(properties), reason, actor), fetch="one")

    def finish_deployment(self, deployment_id, state, detail=None):
        self._run(
            "UPDATE catalog_deployment SET state = %s, detail = %s,"
            " finished_at = now() WHERE id = %s",
            (state, detail, deployment_id), fetch="none")

    def recent_deployments(self, limit=25, catalog_id=None):
        if catalog_id is None:
            return self._run(
                "SELECT * FROM catalog_deployment ORDER BY started_at DESC"
                " LIMIT %s", (limit,))
        return self._run(
            "SELECT * FROM catalog_deployment WHERE catalog_id = %s"
            " ORDER BY started_at DESC LIMIT %s", (catalog_id, limit))


def _row(row):
    if row is None:
        return None
    out = dict(row)
    value = out.get("properties")
    if isinstance(value, str):
        out["properties"] = json.loads(value)
    return out
