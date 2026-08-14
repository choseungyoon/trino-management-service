"""Resource group collection (FR-WORKLOAD).

Data comes from JMX, not from `/v1/resourceGroupState/`. That endpoint accepts
only a root group name - a dotted path is a 404 - and its response stops one
level down, so the tree cannot be built from it (TRINO_VERIFIED.md T1-4,
measured 2026-08-08). Every group that has `jmxExport: true` registers its own
MBean whose `name=` carries the full dotted path, so enumerating the registry
reconstructs the whole hierarchy.

Two properties of the source shape the design:

* Groups are instantiated lazily. A group that has never admitted a query has
  no MBean at all, so this module cannot report the configured set - only the
  set that has seen traffic. Callers must not present the result as "all
  groups"; see `Collection.complete`.
* `HardCpuLimitMillis` and similar default to Long.MAX_VALUE, which means "no
  limit". Comparing usage against that sentinel would mark every healthy group
  as capped, so limits at or above the sentinel threshold are treated as unset.

Python 3.9 compatible.
"""

import logging
from typing import Any, Dict, List, Optional

from tms.clients.errors import TrinoClientError

log = logging.getLogger(__name__)

MBEAN_DOMAIN = "trino.execution.resourcegroups"
GROUP_MBEAN_PREFIX = MBEAN_DOMAIN + ":type=InternalResourceGroup,name="
MANAGER_MBEAN = MBEAN_DOMAIN + ":name=InternalResourceGroupManager"

# Java's Long.MAX_VALUE. Trino uses it for "unlimited", and a limit that large
# is never a real one - treat anything near it as absent.
_UNLIMITED = 9223372036854775807
_UNLIMITED_FLOOR = _UNLIMITED // 2

# Bottleneck reasons, worst first. The order is the priority: a group that is
# rejecting queries outranks one that is merely throttling them.
REJECTING = "queue_full"
CONCURRENCY_CAPPED = "concurrency_limit"
MEMORY_CAPPED = "memory_limit"
CPU_CAPPED = "cpu_limit"


def group_path(object_name: str) -> Optional[List[str]]:
    """'…,name=global.adhoc.dashboard' -> ['global', 'adhoc', 'dashboard']."""
    if not object_name.startswith(GROUP_MBEAN_PREFIX):
        return None
    raw = object_name[len(GROUP_MBEAN_PREFIX):].strip()
    if not raw:
        return None
    return [segment for segment in raw.split(".") if segment]


def _number(attributes: Dict[str, Any], name: str) -> Optional[float]:
    value = attributes.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _limit(attributes: Dict[str, Any], name: str) -> Optional[float]:
    """A limit, or None when it is the 'unlimited' sentinel."""
    value = _number(attributes, name)
    if value is None or value >= _UNLIMITED_FLOOR or value <= 0:
        return None
    return value


def diagnose(summary: Dict[str, Any]) -> Optional[str]:
    """Why is this group holding queries back? (FR-WL-04)

    Only reports a reason when queries are actually waiting. A group sitting at
    its concurrency limit with an empty queue is working exactly as configured
    and must not be flagged - crying wolf here trains operators to ignore the
    one screen that explains a slow cluster.
    """
    queued = summary.get("queued") or 0
    running = summary.get("running") or 0

    max_queued = summary.get("max_queued")
    if max_queued and queued >= max_queued:
        # Past this point Trino rejects new queries outright.
        return REJECTING

    if queued <= 0:
        return None

    hard_concurrency = summary.get("hard_concurrency_limit")
    if hard_concurrency and running >= hard_concurrency:
        return CONCURRENCY_CAPPED

    memory_limit = summary.get("soft_memory_limit_bytes")
    memory_used = summary.get("memory_bytes")
    if memory_limit and memory_used and memory_used >= memory_limit:
        return MEMORY_CAPPED

    cpu_limit = summary.get("hard_cpu_limit_ms")
    cpu_used = summary.get("cpu_ms")
    if cpu_limit and cpu_used and cpu_used >= cpu_limit:
        return CPU_CAPPED

    return None


def summarise_group(path: List[str], attributes: Dict[str, Any]) -> Dict[str, Any]:
    """One group's row. Attribute names verified live @477."""
    summary = {
        "path": list(path),
        "id": ".".join(path),
        "name": path[-1] if path else "",
        "depth": len(path) - 1,
        "running": int(_number(attributes, "RunningQueries") or 0),
        "queued": int(_number(attributes, "QueuedQueries") or 0),
        "waiting_queued": int(_number(attributes, "WaitingQueuedQueries") or 0),
        "hard_concurrency_limit": _limit(attributes, "HardConcurrencyLimit"),
        "soft_concurrency_limit": _limit(attributes, "SoftConcurrencyLimit"),
        "max_queued": _limit(attributes, "MaxQueuedQueries"),
        "soft_memory_limit_bytes": _limit(attributes, "SoftMemoryLimitBytes"),
        "hard_cpu_limit_ms": _limit(attributes, "HardCpuLimitMillis"),
        "memory_bytes": _number(attributes, "MemoryUsageBytes"),
        "cpu_ms": _number(attributes, "CpuUsageMillis"),
        "input_bytes": _number(attributes, "PhysicalInputDataUsageBytes"),
        "scheduling_policy": attributes.get("SchedulingPolicy"),
        "scheduling_weight": _number(attributes, "SchedulingWeight"),
        "started_total": _number(attributes, "StartedQueries.TotalCount"),
    }
    summary["bottleneck"] = diagnose(summary)
    return summary


def build_tree(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Nest the flat list by dotted path.

    A child whose parent has no MBean is promoted to the top rather than
    dropped - losing a busy group because its parent happened not to be
    exported would hide exactly the thing the operator is looking for.
    """
    by_id = {}
    for group in sorted(groups, key=lambda g: g["path"]):
        node = dict(group)
        node["children"] = []
        by_id[node["id"]] = node

    roots = []
    for node in by_id.values():
        parent_id = ".".join(node["path"][:-1])
        parent = by_id.get(parent_id) if parent_id else None
        if parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    return sorted(roots, key=lambda g: g["id"])


def rollup(groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Totals across every collected group, plus who is stuck."""
    blocked = [g for g in groups if g.get("bottleneck")]
    return {
        "groups": len(groups),
        "running": sum(g.get("running") or 0 for g in groups),
        "queued": sum(g.get("queued") or 0 for g in groups),
        "blocked_groups": len(blocked),
        "blocked": sorted(
            ({"id": g["id"], "reason": g["bottleneck"], "queued": g.get("queued") or 0}
             for g in blocked),
            key=lambda item: item["queued"], reverse=True,
        ),
    }


class Collection:
    """The result of one poll.

    `complete` is always False: lazily-created groups mean the set collected is
    "groups that have admitted a query", never "groups that are configured".
    The flag exists so the UI is forced to say so rather than implying a full
    inventory.
    """

    __slots__ = ("groups", "tree", "summary", "error", "advice")

    complete = False

    def __init__(self, groups, error=None, advice=None):
        self.groups = groups
        self.tree = build_tree(groups)
        self.summary = rollup(groups)
        self.error = error
        self.advice = advice

    def as_payload(self) -> Dict[str, Any]:
        return {
            "groups": self.groups,
            "tree": self.tree,
            "summary": self.summary,
            "complete": self.complete,
        }


# Why a configured group is, or is not, visible in JMX. Before D-010 moved the
# configuration into a database, TMS saw only the JMX side and could not tell
# these apart: an absent MBean meant either "no traffic yet" or "jmxExport is
# off", and there was no way to know which. The store answers that now.
STATUS_RUNNING = "running"      # configured, and its MBean is registered
STATUS_IDLE = "idle"            # configured and exported, but never used
STATUS_HIDDEN = "hidden"        # configured with jmx_export off - invisible by design
STATUS_UNMANAGED = "unmanaged"  # an MBean with no row behind it
STATUS_UNKNOWN = "unknown"      # the JMX side was not collected at all


def reconcile(configured, live, live_available=True):
    """Line the configured groups up against the ones actually running.

    Two independent sources answer two different questions - the store says
    what is configured, JMX says what has admitted a query - and an operator
    needs them side by side. A group in one and not the other is the
    interesting case, in both directions.

    `live_available` is False when workload collection is switched off. Every
    status is then UNKNOWN rather than IDLE: "no MBean" is only evidence of
    idleness if someone was looking.
    """
    by_id = {group["id"]: group for group in live or []}

    rows = []
    for group in configured or []:
        row = dict(group)
        observed = by_id.get(group["id"])
        if not live_available:
            row["status"] = STATUS_UNKNOWN
        elif observed is not None:
            row["status"] = STATUS_RUNNING
        elif not group.get("jmx_export"):
            row["status"] = STATUS_HIDDEN
        else:
            row["status"] = STATUS_IDLE
        row["running"] = (observed or {}).get("running")
        row["queued"] = (observed or {}).get("queued")
        row["bottleneck"] = (observed or {}).get("bottleneck")
        rows.append(row)

    # Groups running with nothing behind them. Either someone edited the
    # database by hand, or `node.environment` does not match what this
    # coordinator actually runs with - both worth surfacing rather than
    # quietly dropping, since the second means the whole screen is describing
    # the wrong cluster.
    configured_ids = {group["id"] for group in configured or []}
    unmanaged = []
    if live_available:
        for group in live or []:
            if group["id"] in configured_ids:
                continue
            row = dict(group)
            row["status"] = STATUS_UNMANAGED
            unmanaged.append(row)

    return rows, unmanaged


NO_GROUPS_ADVICE = (
    "No resource group MBeans are registered. Either no group has admitted a "
    "query yet, or resource-groups.json is missing \"jmxExport\": true - groups "
    "without it are invisible to TMS. See DESIGN_R2.md 1-2."
)


def collect(client) -> Collection:
    """Enumerate the registry, then read each group's MBean.

    One failed MBean does not fail the poll: the rest still render, and the
    failure is recorded per group.
    """
    try:
        names = client.list_mbean_names()
    except TrinoClientError as exc:
        return Collection([], error="{}: {}".format(type(exc).__name__, exc),
                          advice=exc.advice or None)

    paths = []
    for name in names:
        path = group_path(name)
        if path:
            paths.append((name, path))

    if not paths:
        return Collection([], error="no resource group MBeans registered",
                          advice=NO_GROUPS_ADVICE)

    groups = []
    failures = []
    advice = None
    for object_name, path in paths:
        try:
            attributes = client.get_mbean(object_name)
        except TrinoClientError as exc:
            failures.append(".".join(path))
            if advice is None and exc.advice:
                advice = exc.advice
            log.warning("resource group MBean read failed for %s: %s", object_name, exc)
            continue
        groups.append(summarise_group(path, attributes))

    error = None
    if failures:
        error = "{} of {} resource group MBeans unavailable".format(
            len(failures), len(paths))
    return Collection(groups, error=error, advice=advice)
