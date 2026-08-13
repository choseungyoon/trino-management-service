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
from typing import Any, Optional

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


class ResourceGroupStore:
    """Reads Trino's resource group tables. Never writes.

    Writing is FR-WL-07's job and will use its own account; this exists only so
    the restart sequence can look before it leaps.
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
