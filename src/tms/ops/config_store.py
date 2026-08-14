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

import json
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

# What an edit may change. `parent` and `environment` are absent on purpose:
# moving a group between parents or clusters is a different tree, not a
# different value, and it would silently re-target every selector beneath it.
_EDITABLE_GROUP_COLUMNS = (
    "name", "soft_memory_limit", "soft_concurrency_limit",
    "hard_concurrency_limit", "max_queued", "scheduling_policy",
    "scheduling_weight", "jmx_export", "soft_cpu_limit", "hard_cpu_limit",
    "hard_physical_data_scan_limit",
)


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


def _shape(group_rows, selector_rows):
    """Raw rows -> the shape the rest of TMS speaks (dotted paths and matchers)."""
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
            "target_row_id": row["resource_group_id"],
            "matchers": matchers,
            "catch_all": not matchers,
        })
    return groups, selectors


class ChangeRejected(Exception):
    """The tree the change would produce is not one Trino should be given.

    Carries the findings so the screen can show every reason at once rather
    than making someone fix them one save at a time.
    """

    def __init__(self, findings):
        self.findings = findings
        super().__init__("; ".join(f.message for f in findings))


class ChangeResult:
    """A committed change, and anything worth saying about the result."""

    __slots__ = ("tree_before", "tree_after", "warnings", "revision_id")

    def __init__(self, tree_before, tree_after, warnings, revision_id=None):
        self.tree_before = tree_before
        self.tree_after = tree_after
        self.warnings = warnings
        self.revision_id = revision_id


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


    # ------------------------------------------------------------- reading

    def _fetch(self, cursor, environment: str):
        """Groups and selectors for one environment, already shaped.

        Takes a cursor rather than opening its own connection: a write has to
        read the tree before and after its own uncommitted change, and doing
        that on a second connection would read the pre-change state both times.
        """
        cursor.execute(
            "SELECT {cols} FROM {schema}.resource_groups WHERE environment = %s".format(
                cols=", ".join(_GROUP_COLUMNS), schema=self._schema),
            (environment,))
        group_rows = [dict(zip(_GROUP_COLUMNS, row)) for row in cursor.fetchall() or []]

        cursor.execute(
            ("SELECT {cols} FROM {schema}.selectors s"
             " JOIN {schema}.resource_groups g"
             "   ON s.resource_group_id = g.resource_group_id"
             " WHERE g.environment = %s"
             " ORDER BY s.priority DESC, s.id").format(
                 cols=", ".join("s." + c for c in _SELECTOR_COLUMNS),
                 schema=self._schema),
            (environment,))
        selector_rows = [dict(zip(_SELECTOR_COLUMNS, row))
                         for row in cursor.fetchall() or []]

        return _shape(group_rows, selector_rows)

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

        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    groups, selectors = self._fetch(cursor, environment)
        except Exception as exc:  # noqa: BLE001 - a screen, not a crash
            log.warning("resource group store read failed for %s: %s", environment, exc)
            return ConfiguredTree([], [], error=_unreachable_detail(exc, self._schema))

        error = None
        advice = None
        if not groups:
            error = "no resource groups configured for '{}'".format(environment)
            advice = NO_ROWS_ADVICE.format(env=environment)
        return ConfiguredTree(groups, selectors, error=error, advice=advice)

    def deletion_impact(self, environment: str, row_id) -> Dict[str, Any]:
        """Everything that would go with this row.

        Both foreign keys are ON DELETE CASCADE, so removing a root group takes
        its whole subtree and every selector pointing into it. The screen lists
        what disappears rather than counting it: a count is something people
        accept, a list is something they read.
        """
        tree = self.load_configured(environment)
        target = next((g for g in tree.groups if g.get("row_id") == row_id), None)
        if target is None:
            return {"group": None, "groups": [], "selectors": []}

        prefix = target["id"] + "."
        doomed = [g for g in tree.groups
                  if g["id"] == target["id"] or g["id"].startswith(prefix)]
        doomed_ids = {g["id"] for g in doomed}
        return {
            "group": target,
            "groups": doomed,
            "selectors": [s for s in tree.selectors if s.get("target") in doomed_ids],
        }

    def revisions(self, environment: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Change history, newest first. Empty when the store cannot be read."""
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id, occurred_at, actor, reason, kind, target"
                        "  FROM resource_group_revision"
                        " WHERE environment = %s"
                        " ORDER BY occurred_at DESC, id DESC"
                        " LIMIT %s",
                        (environment, int(limit)))
                    rows = cursor.fetchall() or []
        except Exception as exc:  # noqa: BLE001
            log.warning("revision history unavailable for %s: %s", environment, exc)
            return []
        return [{"id": r[0], "occurred_at": r[1], "actor": r[2], "reason": r[3],
                 "kind": r[4], "target": r[5]} for r in rows]

    # ------------------------------------------------------------- writing

    def _connect_tx(self) -> Any:
        import psycopg

        return psycopg.connect(
            self._dsn, autocommit=False, connect_timeout=CONNECT_TIMEOUT_SECONDS)

    def _apply(self, environment: str, kind: str, target: str, actor: str,
               reason: str, request_id: str, mutate,
               group_provider_configured: bool = False,
               target_row_id=None) -> ChangeResult:
        """Run one mutation inside one transaction, or none of it.

        The order is deliberate:

        1. take an advisory lock on the environment, so two administrators
           editing the same cluster serialise rather than interleave;
        2. read the tree as it stands;
        3. mutate;
        4. read the tree back and validate **what the change produced**, not
           what was submitted - the rules that matter are relational, and a
           single field can be valid while the tree it lands in is not;
        5. record the before and after alongside the change itself.

        Step 5 shares this transaction, so a committed change always has its
        snapshot. The audit row does not - it is written by the guard around
        this call, audit-first with an outcome recorded on the way out, exactly
        as every other write action in TMS works.
        """
        from tms.ops.resource_group_rules import blocking, validate

        connection = self._connect_tx()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                               (environment,))
                before_groups, before_selectors = self._fetch(cursor, environment)
                if target_row_id is not None:
                    # Record what the operator sees, not the primary key. A
                    # history row reading "group update 2" is a row nobody can
                    # use; resolving it here is the only moment the group still
                    # exists to be named.
                    named = next((g for g in before_groups
                                  if str(g["row_id"]) == str(target_row_id)), None)
                    if named is not None:
                        target = named["id"]
                mutate(cursor)
                after_groups, after_selectors = self._fetch(cursor, environment)

                findings = validate(after_groups, after_selectors,
                                    group_provider_configured=group_provider_configured)
                errors = blocking(findings)
                if errors:
                    connection.rollback()
                    raise ChangeRejected(errors)

                before = {"groups": before_groups, "selectors": before_selectors}
                after = {"groups": after_groups, "selectors": after_selectors}
                cursor.execute(
                    "INSERT INTO resource_group_revision"
                    " (environment, actor, reason, request_id, kind, target,"
                    "  tree_before, tree_after)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)"
                    " RETURNING id",
                    (environment, actor, reason, request_id, kind, target,
                     json.dumps(before, default=str), json.dumps(after, default=str)))
                revision_id = (cursor.fetchone() or [None])[0]

            connection.commit()
            return ChangeResult(before, after,
                                [f for f in findings if not f.blocking], revision_id)
        except ChangeRejected:
            raise
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_group(self, environment, row_id, changes, actor, reason, request_id,
                     group_provider_configured=False) -> ChangeResult:
        """Change one group's values. Structure is create/delete, not update."""
        fields = {name: value for name, value in changes.items()
                  if name in _EDITABLE_GROUP_COLUMNS}
        if not fields:
            raise ChangeRejected([_no_op_finding()])

        assignments = ", ".join("{} = %s".format(name) for name in sorted(fields))
        params = [fields[name] for name in sorted(fields)] + [row_id, environment]

        def mutate(cursor):
            cursor.execute(
                "UPDATE {schema}.resource_groups SET {sets}"
                " WHERE resource_group_id = %s AND environment = %s".format(
                    schema=self._schema, sets=assignments),
                params)
            if cursor.rowcount != 1:
                # Scoped by environment as well as id, so a stale screen cannot
                # edit a row belonging to the other cluster.
                raise ChangeRejected([_missing_row_finding(row_id, environment)])

        return self._apply(environment, "group_update", str(row_id), actor, reason,
                           request_id, mutate, group_provider_configured,
                           target_row_id=row_id)

    def create_group(self, environment, name, parent_row_id, values, actor, reason,
                     request_id, group_provider_configured=False) -> ChangeResult:
        fields = {"name": name, "environment": environment, "parent": parent_row_id}
        fields.update({k: v for k, v in (values or {}).items()
                       if k in _EDITABLE_GROUP_COLUMNS and k != "name"})
        columns = sorted(fields)

        def mutate(cursor):
            cursor.execute(
                "INSERT INTO {schema}.resource_groups ({cols}) VALUES ({marks})".format(
                    schema=self._schema, cols=", ".join(columns),
                    marks=", ".join(["%s"] * len(columns))),
                [fields[c] for c in columns])

        return self._apply(environment, "group_create", str(name), actor, reason,
                           request_id, mutate, group_provider_configured)

    def delete_group(self, environment, row_id, actor, reason, request_id,
                     group_provider_configured=False) -> ChangeResult:
        """Delete a group and, by cascade, everything beneath it.

        No attempt is made to soften the cascade - both foreign keys carry it,
        and re-implementing the deletion by hand to delete less would leave
        orphans Trino would then read. The screen's job is to show what goes;
        this one's is to do exactly what the schema says.
        """
        def mutate(cursor):
            cursor.execute(
                "DELETE FROM {schema}.resource_groups"
                " WHERE resource_group_id = %s AND environment = %s".format(
                    schema=self._schema),
                (row_id, environment))
            if cursor.rowcount != 1:
                raise ChangeRejected([_missing_row_finding(row_id, environment)])

        return self._apply(environment, "group_delete", str(row_id), actor, reason,
                           request_id, mutate, group_provider_configured,
                           target_row_id=row_id)

    def create_selector(self, environment, target_row_id, priority, matchers, actor,
                        reason, request_id,
                        group_provider_configured=False) -> ChangeResult:
        fields = {"resource_group_id": target_row_id, "priority": int(priority)}
        fields.update({k: v for k, v in (matchers or {}).items()
                       if k in _MATCHER_COLUMNS and v not in (None, "")})
        columns = sorted(fields)

        def mutate(cursor):
            cursor.execute(
                "INSERT INTO {schema}.selectors ({cols}) VALUES ({marks})".format(
                    schema=self._schema, cols=", ".join(columns),
                    marks=", ".join(["%s"] * len(columns))),
                [fields[c] for c in columns])

        return self._apply(environment, "selector_create", str(target_row_id), actor,
                           reason, request_id, mutate, group_provider_configured,
                           target_row_id=target_row_id)

    def delete_selector(self, environment, selector_id, actor, reason, request_id,
                        group_provider_configured=False) -> ChangeResult:
        """Remove one selector.

        Removing the last catch-all is not special-cased here. Validation runs
        on the resulting tree and V10 refuses it, which means the rule lives in
        one place instead of being restated at every call site.
        """
        def mutate(cursor):
            cursor.execute(
                "DELETE FROM {schema}.selectors s"
                " USING {schema}.resource_groups g"
                " WHERE s.resource_group_id = g.resource_group_id"
                "   AND s.id = %s AND g.environment = %s".format(schema=self._schema),
                (selector_id, environment))
            if cursor.rowcount != 1:
                raise ChangeRejected([_missing_row_finding(selector_id, environment)])

        return self._apply(environment, "selector_delete", str(selector_id), actor,
                           reason, request_id, mutate, group_provider_configured)

    def revert(self, environment, revision_id, actor, reason, request_id,
               group_provider_configured=False) -> ChangeResult:
        """Put the environment back to how a revision found it.

        Appends a new revision rather than removing the ones in between - the
        history is a record of what was done, and a record that can be rewritten
        is not one. Row ids change, which is harmless: nothing outside these two
        tables refers to them, and the selectors are rebuilt in the same pass.
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT tree_before FROM resource_group_revision"
                    " WHERE id = %s AND environment = %s",
                    (revision_id, environment))
                row = cursor.fetchone()
        if row is None:
            raise ChangeRejected([_missing_row_finding(revision_id, environment)])
        snapshot = row[0] if isinstance(row[0], dict) else json.loads(row[0])

        def mutate(cursor):
            self._restore(cursor, environment, snapshot)

        return self._apply(environment, "revert", str(revision_id), actor, reason,
                           request_id, mutate, group_provider_configured)

    def _restore(self, cursor, environment, snapshot) -> None:
        """Replace an environment's rows with a snapshot's."""
        cursor.execute(
            "DELETE FROM {schema}.resource_groups WHERE environment = %s".format(
                schema=self._schema),
            (environment,))

        # Parents before children: `parent` is a foreign key into this same
        # table, so depth order is not cosmetic.
        new_id_of = {}
        for group in sorted(snapshot.get("groups") or [],
                            key=lambda g: len(g.get("path") or [])):
            parent_path = ".".join((group.get("path") or [])[:-1])
            fields = {"name": group.get("name"), "environment": environment,
                      "parent": new_id_of.get(parent_path)}
            fields.update({c: group.get(c) for c in _EDITABLE_GROUP_COLUMNS
                           if c != "name" and group.get(c) is not None})
            columns = sorted(fields)
            cursor.execute(
                "INSERT INTO {schema}.resource_groups ({cols}) VALUES ({marks})"
                " RETURNING resource_group_id".format(
                    schema=self._schema, cols=", ".join(columns),
                    marks=", ".join(["%s"] * len(columns))),
                [fields[c] for c in columns])
            new_id_of[group.get("id")] = (cursor.fetchone() or [None])[0]

        for selector in snapshot.get("selectors") or []:
            target = new_id_of.get(selector.get("target"))
            if target is None:
                continue
            fields = {"resource_group_id": target,
                      "priority": selector.get("priority")}
            fields.update({k: v for k, v in (selector.get("matchers") or {}).items()
                           if k in _MATCHER_COLUMNS})
            columns = sorted(fields)
            cursor.execute(
                "INSERT INTO {schema}.selectors ({cols}) VALUES ({marks})".format(
                    schema=self._schema, cols=", ".join(columns),
                    marks=", ".join(["%s"] * len(columns))),
                [fields[c] for c in columns])


def _no_op_finding():
    from tms.ops.resource_group_rules import ERROR, Finding

    return Finding(ERROR, "V0", "-", "Nothing was changed.")


def _missing_row_finding(row_id, environment):
    from tms.ops.resource_group_rules import ERROR, Finding

    return Finding(
        ERROR, "V0", str(row_id),
        "No such row in '{}'. It may have been deleted since this screen "
        "was loaded.".format(environment))


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
