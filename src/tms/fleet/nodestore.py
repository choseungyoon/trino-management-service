"""Where the node list lives now that it is not a hand-edited file (D-019).

Two ways a row gets here, and they are not the same thing:

* `discovered` - the coordinator reported it. Refreshed on every scan.
* `manual` - a person added it, with a reason, because it is down and
  therefore invisible to discovery while still needing configuration.

⛔ `refresh` adds and touches. It never deletes. A node that stops appearing in
`system.runtime.nodes` is either decommissioned or *down*, and nothing here can
tell those apart - removing it automatically would quietly drop a down node out
of every later deployment. Removal is `remove()`, which the service only
reaches with a reason and an audit row.

Python 3.9 compatible.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SOURCE_DISCOVERED = "discovered"
SOURCE_MANUAL = "manual"


class NodeStoreUnavailable(Exception):
    """The node table could not be reached."""


class DuplicateNode(Exception):
    """That host is already listed for that cluster."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryNodeRepository:
    """Test double with the same guarantees the schema provides."""

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []
        self._next = 1

    def _find(self, cluster, host):
        for row in self.rows:
            if row["cluster"] == cluster and row["host"] == host:
                return row
        return None

    def list(self, cluster: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = [r for r in self.rows if cluster is None or r["cluster"] == cluster]
        # Coordinator first, then workers by name: the order somebody reading
        # the screen expects, and the order the inventory renders in.
        return [dict(r) for r in sorted(
            rows, key=lambda r: (r["cluster"], r["role"] != "coordinator", r["host"]))]

    def add(self, cluster, host, address, role, source, actor,
            reason=None, node_id=None, version=None, last_seen_at=None):
        if self._find(cluster, host) is not None:
            raise DuplicateNode(host)
        row = {"id": self._next, "cluster": cluster, "host": host,
               "address": address, "role": role, "source": source,
               "reason": reason, "added_by": actor, "node_id": node_id,
               "version": version, "last_seen_at": last_seen_at,
               "created_at": utcnow()}
        self._next += 1
        self.rows.append(row)
        return dict(row)

    def touch(self, cluster, host, **changes):
        row = self._find(cluster, host)
        if row is None:
            return None
        row.update(changes)
        return dict(row)

    def remove(self, cluster, host) -> bool:
        row = self._find(cluster, host)
        if row is None:
            return False
        self.rows.remove(row)
        return True


class PostgresNodeRepository:
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
        except psycopg.errors.UniqueViolation:
            raise DuplicateNode("that host is already listed for this cluster")
        except Exception as exc:  # noqa: BLE001
            raise NodeStoreUnavailable(str(exc))

    def list(self, cluster: Optional[str] = None):
        if cluster is None:
            return self._run(
                "SELECT * FROM cluster_node"
                " ORDER BY cluster, role <> 'coordinator', host")
        return self._run(
            "SELECT * FROM cluster_node WHERE cluster = %s"
            " ORDER BY role <> 'coordinator', host", (cluster,))

    def add(self, cluster, host, address, role, source, actor,
            reason=None, node_id=None, version=None, last_seen_at=None):
        return self._run(
            "INSERT INTO cluster_node"
            " (cluster, host, address, role, source, added_by, reason,"
            "  node_id, version, last_seen_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (cluster, host, address, role, source, actor, reason,
             node_id, version, last_seen_at), fetch="one")

    def touch(self, cluster, host, **changes):
        if not changes:
            return None
        columns = ", ".join("{} = %s".format(c) for c in changes)
        values = list(changes.values()) + [cluster, host]
        return self._run(
            "UPDATE cluster_node SET {} WHERE cluster = %s AND host = %s"
            " RETURNING *".format(columns), tuple(values), fetch="one")

    def remove(self, cluster, host):
        return bool(self._run(
            "DELETE FROM cluster_node WHERE cluster = %s AND host = %s"
            " RETURNING id", (cluster, host), fetch="one"))
