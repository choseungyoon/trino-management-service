"""Can Trino's resource group store serve a coordinator right now? (D-010)

Trino 477's `db` resource group manager runs its Flyway migration synchronously
on the main thread, *before* the HTTP server binds. If the database is
unreachable the coordinator does not come up degraded - it exits, with no retry
and no backoff (measured 2026-08-13, TRINO_VERIFIED.md T1-4-1). A coordinator
that is already running tolerates the same outage indefinitely and self-heals
when the database returns.

So the danger is narrow and specific: **restarting a cluster while the store is
unusable turns a recoverable database incident into a cluster that cannot be
brought back up** - and the safe sequence has by then already stopped traffic to
it. This module answers the one question that lets the sequence refuse.

It checks more than reachability, because reachability is largely covered
already: the audit guard and the sequence repository both live in the same
database and both refuse to work when it is down. What is *not* covered, and
what this catches, is a store that is up but cannot serve this particular
cluster - the schema was never created, or rows were inserted for `cluster1`
and nobody ran the script for `cluster2`. Trino matches rows on `environment`,
so a coordinator whose `node.environment` has no rows finds no groups and no
selectors.

Python 3.9 compatible.
"""

import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# A schema name reaches SQL by interpolation - it cannot be a bound parameter -
# so it is constrained to a plain unquoted identifier. The value comes from
# config rather than from a request, but "not user input today" is not a
# property that survives refactoring.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Short on purpose. This runs while an operator is watching a restart screen,
# and a store that needs longer than this to answer is not one a coordinator
# should be restarted against.
CONNECT_TIMEOUT_SECONDS = 3


def valid_schema_name(name: str) -> bool:
    return bool(_IDENTIFIER.match(name or ""))


class StoreProbe:
    """The answer, plus the sentence to show the operator.

    `ready` is deliberately three-valued:

    * ``True``  - a coordinator would find its configuration.
    * ``False`` - it would not. The restart must not proceed.
    * ``None``  - TMS has no opinion, because the check is switched off or the
      cluster has no `node.environment` configured. Unknown is not the same as
      safe, but blocking every restart because an optional setting is absent
      would make the feature worse than not having it.
    """

    __slots__ = ("ready", "detail")

    def __init__(self, ready: Optional[bool], detail: str) -> None:
        self.ready = ready
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "StoreProbe(ready={!r}, detail={!r})".format(self.ready, self.detail)


UNCONFIGURED = StoreProbe(
    None, "TMS is not checking the resource group store for this cluster.")


# Column order is pinned rather than SELECT *: the schema is Trino's, and a
# future release adding a column should not silently shift what TMS reads.
_GROUP_COLUMNS = (
    "resource_group_id", "name", "parent", "soft_memory_limit",
    "soft_concurrency_limit", "hard_concurrency_limit", "max_queued",
    "scheduling_policy", "scheduling_weight", "jmx_export", "soft_cpu_limit",
    "hard_cpu_limit", "hard_physical_data_scan_limit",
)

# Every way a selector can narrow what it matches. A row with all of these null
# is the catch-all, and there must always be one - Trino 477 does not document
# what happens to a query that matches no selector (DESIGN_WL07.md V10).
_MATCHER_COLUMNS = (
    "user_regex", "user_group_regex", "source_regex", "query_type",
    "client_tags", "original_user_regex", "authenticated_user_regex",
)

_SELECTOR_COLUMNS = ("id", "priority", "resource_group_id") + _MATCHER_COLUMNS


class ConfiguredTree:
    """What the store says is configured for one `node.environment`.

    Distinct from the JMX view in `collector/resourcegroups.py`, which reports
    groups that have *admitted a query*. A group configured but never used has
    no MBean at all, so until D-010 moved the configuration into a database TMS
    could not report the configured set (DESIGN_R2.md 1-3). This is that set.
    """

    __slots__ = ("groups", "tree", "selectors", "error", "advice")

    def __init__(self, groups, selectors, error=None, advice=None):
        from tms.collector.resourcegroups import build_tree

        self.groups = groups
        self.tree = build_tree(groups)
        self.selectors = selectors
        self.error = error
        self.advice = advice

    @property
    def catch_all(self):
        """The selector that matches everything, or None - see V10."""
        for selector in self.selectors:
            if selector.get("catch_all"):
                return selector
        return None

    def as_payload(self) -> Dict[str, Any]:
        return {
            "groups": self.groups,
            "tree": self.tree,
            "selectors": self.selectors,
            "has_catch_all": self.catch_all is not None,
        }


def _dotted_paths(rows: List[Dict[str, Any]]) -> Dict[Any, List[str]]:
    """Row id -> ['global', 'adhoc'], following `parent` upwards.

    Trino stores the hierarchy as parent ids; every other part of TMS - the JMX
    MBean names, the workload screen, the selectors' targets - speaks dotted
    paths. Converting once here keeps that one representation everywhere.
    """
    by_id = {row["resource_group_id"]: row for row in rows}
    paths: Dict[Any, List[str]] = {}

    def resolve(row_id, seen):
        if row_id in paths:
            return paths[row_id]
        row = by_id.get(row_id)
        if row is None or row_id in seen:
            # A missing parent or a cycle. Neither should exist, but reading a
            # table TMS does not own means neither can be assumed away, and
            # recursing forever is a worse failure than a shortened path.
            return []
        seen.add(row_id)
        parent = row.get("parent")
        prefix = resolve(parent, seen) if parent is not None else []
        paths[row_id] = prefix + [row["name"]]
        return paths[row_id]

    for row in rows:
        resolve(row["resource_group_id"], set())
    return paths


class ResourceGroupStore:
    """Reads Trino's resource group tables. Never writes.

    Writing is FR-WL-08's job and will use its own account (DESIGN_WL07.md
    H-1); this reads, so that the restart sequence can look before it leaps and
    so the configured tree can be shown next to the running one.
    """

    def __init__(self, dsn: str, schema: str) -> None:
        if not valid_schema_name(schema):
            raise ValueError(
                "resource_groups.schema must be a plain SQL identifier, got {!r}"
                .format(schema))
        self._dsn = dsn
        self._schema = schema

    def _connect(self) -> Any:
        import psycopg  # lazy: keeps this module importable without a driver

        return psycopg.connect(
            self._dsn, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS)

    def probe(self, environment: str) -> StoreProbe:
        """Would a coordinator with this `node.environment` find its config?

        Never raises. A probe that blew up would take down the restart screen
        instead of answering the question it was asked, and "TMS crashed while
        checking" is not a useful thing to tell someone mid-incident.
        """
        if not (environment or "").strip():
            return UNCONFIGURED

        environment = environment.strip()
        query = (
            "SELECT"
            " (SELECT count(*) FROM {schema}.resource_groups"
            "   WHERE environment = %s),"
            " (SELECT count(*) FROM {schema}.selectors s"
            "   JOIN {schema}.resource_groups g"
            "     ON s.resource_group_id = g.resource_group_id"
            "  WHERE g.environment = %s)"
        ).format(schema=self._schema)

        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, (environment, environment))
                    row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001 - any failure is a blocked restart
            log.warning("resource group store probe failed for %s: %s",
                        environment, exc)
            return StoreProbe(False, _unreachable_detail(exc, self._schema))

        groups = int((row or (0, 0))[0] or 0)
        selectors = int((row or (0, 0))[1] or 0)

        if groups == 0:
            return StoreProbe(False, (
                "The resource group store has no rows for node.environment "
                "'{env}'. A coordinator restarted now would come up with no "
                "resource groups. Load this cluster's rows first."
            ).format(env=environment))

        if selectors == 0:
            return StoreProbe(False, (
                "The resource group store has {groups} group(s) for "
                "node.environment '{env}' but no selectors, so no query would "
                "be routed to any of them."
            ).format(groups=groups, env=environment))

        return StoreProbe(True, (
            "Resource group store reachable: {groups} group(s), {selectors} "
            "selector(s) for node.environment '{env}'."
        ).format(groups=groups, selectors=selectors, env=environment))


    def load_configured(self, environment: str) -> ConfiguredTree:
        """The configured groups and selectors for one environment.

        Never raises, for the same reason `probe` does not: this feeds a screen
        an operator opens during an incident, and a traceback where the tree
        should be answers nothing.
        """
        if not (environment or "").strip():
            return ConfiguredTree([], [], error="no node_environment configured",
                                  advice=NO_ENVIRONMENT_ADVICE)
        environment = environment.strip()

        group_sql = "SELECT {cols} FROM {schema}.resource_groups WHERE environment = %s".format(
            cols=", ".join(_GROUP_COLUMNS), schema=self._schema)
        selector_sql = (
            "SELECT {cols} FROM {schema}.selectors s"
            " JOIN {schema}.resource_groups g"
            "   ON s.resource_group_id = g.resource_group_id"
            " WHERE g.environment = %s"
            " ORDER BY s.priority DESC, s.id"
        ).format(cols=", ".join("s." + c for c in _SELECTOR_COLUMNS),
                 schema=self._schema)

        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(group_sql, (environment,))
                    group_rows = [dict(zip(_GROUP_COLUMNS, row))
                                  for row in cursor.fetchall() or []]
                    cursor.execute(selector_sql, (environment,))
                    selector_rows = [dict(zip(_SELECTOR_COLUMNS, row))
                                     for row in cursor.fetchall() or []]
        except Exception as exc:  # noqa: BLE001 - a screen, not a crash
            log.warning("resource group store read failed for %s: %s", environment, exc)
            return ConfiguredTree([], [], error=_unreachable_detail(exc, self._schema))

        paths = _dotted_paths(group_rows)
        groups = []
        for row in group_rows:
            path = paths.get(row["resource_group_id"]) or [row["name"]]
            groups.append({
                "id": ".".join(path),
                "path": path,
                "name": row["name"],
                "depth": len(path) - 1,
                "row_id": row["resource_group_id"],
                "jmx_export": bool(row.get("jmx_export")),
                "hard_concurrency_limit": row.get("hard_concurrency_limit"),
                "soft_concurrency_limit": row.get("soft_concurrency_limit"),
                "max_queued": row.get("max_queued"),
                "soft_memory_limit": row.get("soft_memory_limit"),
                "soft_cpu_limit": row.get("soft_cpu_limit"),
                "hard_cpu_limit": row.get("hard_cpu_limit"),
                "hard_physical_data_scan_limit": row.get("hard_physical_data_scan_limit"),
                "scheduling_policy": row.get("scheduling_policy"),
                "scheduling_weight": row.get("scheduling_weight"),
            })

        target_of = {g["row_id"]: g["id"] for g in groups}
        selectors = []
        for row in selector_rows:
            matchers = {name: row.get(name) for name in _MATCHER_COLUMNS
                        if row.get(name) not in (None, "")}
            selectors.append({
                "id": row["id"],
                "priority": row["priority"],
                "target": target_of.get(row["resource_group_id"], ""),
                "matchers": matchers,
                "catch_all": not matchers,
            })

        error = None
        advice = None
        if not groups:
            error = "no resource groups configured for '{}'".format(environment)
            advice = NO_ROWS_ADVICE.format(env=environment)
        return ConfiguredTree(groups, selectors, error=error, advice=advice)


NO_ENVIRONMENT_ADVICE = (
    "This cluster has no `node_environment` in config.yaml, so TMS cannot tell "
    "which rows in the store belong to it. Copy the value from the "
    "coordinator's node.properties."
)

NO_ROWS_ADVICE = (
    "The store holds no rows for node.environment '{env}'. Either the value "
    "does not match the coordinator's node.properties, or this cluster's rows "
    "were never loaded - see runbooks/resource-groups-db.md."
)


def _unreachable_detail(exc: Exception, schema: str) -> str:
    """Turn a driver exception into something worth reading at 3am.

    The distinction that matters to the operator is "the database is down" (wait
    for it, then retry) versus "the tables are not there" (someone skipped a
    setup step) - those have completely different next actions.
    """
    text = str(exc)
    lowered = text.lower()
    if "does not exist" in lowered or "undefined" in lowered:
        return (
            "The resource group tables were not found in schema '{schema}'. "
            "Restarting now would leave the coordinator unable to start. "
            "Original error: {text}"
        ).format(schema=schema, text=text)
    return (
        "The resource group store is unreachable, and a Trino 477 coordinator "
        "will not start without it. Restore the database before restarting. "
        "Original error: {text}"
    ).format(text=text)
