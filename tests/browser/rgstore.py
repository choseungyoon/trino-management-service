"""An in-memory stand-in for Trino's resource group tables.

The browser harness runs with no PostgreSQL and no Trino on purpose - a
verification tool that is heavier than the thing it verifies does not get run.
This keeps that property for the resource group screens while still exercising
the real validation rules, the real revision bookkeeping and the real refusals:
only the storage is fake.

The SQL those methods replace is covered where SQL can actually be tested, in
tests/integration/smoke_resource_groups.py.

Python 3.9 compatible.
"""

import copy
import itertools
from datetime import datetime, timedelta, timezone

from tms.ops.config_store import ChangeRejected, ChangeResult, ConfiguredTree, StoreProbe
from tms.ops.resource_group_rules import blocking, validate

# The tree docs/templates/resource-group.json produces, so the screenshots show
# the configuration the runbook actually installs.
SEED_GROUPS = [
    {"row_id": 1, "name": "global", "path": ["global"], "parent": None,
     "soft_memory_limit": "80%", "hard_concurrency_limit": 100, "max_queued": 1000,
     "jmx_export": True, "scheduling_policy": None},
    {"row_id": 2, "name": "${USER}", "path": ["global", "${USER}"], "parent": 1,
     "soft_memory_limit": "30%", "hard_concurrency_limit": 8, "max_queued": 100,
     "jmx_export": False, "scheduling_policy": None},
    {"row_id": 3, "name": "admin", "path": ["admin"], "parent": None,
     "soft_memory_limit": None, "hard_concurrency_limit": 20, "max_queued": 100,
     "jmx_export": True, "scheduling_policy": None},
]

SEED_SELECTORS = [
    {"id": 10, "priority": 20, "target_row_id": 3,
     "matchers": {"user_regex": r"^datalake\.admin$"}},
    {"id": 11, "priority": 10, "target_row_id": 2, "matchers": {}},
]

SEED_REVISIONS = [
    {"id": 1, "actor": "sre.kim", "reason": "Superset dashboards were queueing "
                                            "behind one another",
     "kind": "group_update", "target": "global.${USER}", "minutes_ago": 95},
    {"id": 2, "actor": "sre.kim", "reason": "admin needs headroom during the "
                                            "migration window",
     "kind": "group_update", "target": "admin", "minutes_ago": 20},
]


def _now():
    return datetime.now(timezone.utc)


class InMemoryResourceGroupStore:
    def __init__(self):
        self.groups = copy.deepcopy(SEED_GROUPS)
        self.selectors = copy.deepcopy(SEED_SELECTORS)
        self._revisions = [
            dict(row, occurred_at=_now() - timedelta(minutes=row["minutes_ago"]))
            for row in copy.deepcopy(SEED_REVISIONS)
        ]
        self._ids = itertools.count(100)
        self._snapshots = {}

    # ------------------------------------------------------------- reading

    def _shape(self):
        by_row = {g["row_id"]: g for g in self.groups}
        groups = []
        for row in self.groups:
            path = list(row["path"])
            groups.append({
                "id": ".".join(path), "path": path, "name": row["name"],
                "depth": len(path) - 1, "row_id": row["row_id"],
                "jmx_export": row["jmx_export"],
                "hard_concurrency_limit": row["hard_concurrency_limit"],
                "soft_concurrency_limit": None,
                "max_queued": row["max_queued"],
                "soft_memory_limit": row["soft_memory_limit"],
                "soft_cpu_limit": None, "hard_cpu_limit": None,
                "hard_physical_data_scan_limit": None,
                "scheduling_policy": row.get("scheduling_policy"),
                "scheduling_weight": None,
            })
        target_of = {row["row_id"]: ".".join(row["path"]) for row in by_row.values()}
        selectors = [
            {"id": s["id"], "priority": s["priority"],
             "target": target_of.get(s["target_row_id"], ""),
             "target_row_id": s["target_row_id"],
             "matchers": dict(s["matchers"]), "catch_all": not s["matchers"]}
            for s in sorted(self.selectors, key=lambda s: (-s["priority"], s["id"]))
        ]
        return groups, selectors

    def load_configured(self, environment):
        if not (environment or "").strip():
            return ConfiguredTree([], [], error="no node_environment configured")
        if environment != "cluster1":
            # Only one cluster is seeded, so the other shows the "rows were
            # never loaded" state - which is a screen worth being able to see.
            return ConfiguredTree(
                [], [], error="no resource groups configured for '{}'".format(environment),
                advice="This cluster's rows were never loaded.")
        groups, selectors = self._shape()
        return ConfiguredTree(groups, selectors)

    def probe(self, environment):
        tree = self.load_configured(environment)
        if not tree.groups:
            return StoreProbe(False, "No rows for '{}'.".format(environment))
        return StoreProbe(True, "{} groups configured.".format(len(tree.groups)))

    def deletion_impact(self, environment, row_id):
        groups, selectors = self._shape()
        target = next((g for g in groups if str(g["row_id"]) == str(row_id)), None)
        if target is None:
            return {"group": None, "groups": [], "selectors": []}
        prefix = target["id"] + "."
        doomed = [g for g in groups
                  if g["id"] == target["id"] or g["id"].startswith(prefix)]
        ids = {g["id"] for g in doomed}
        return {"group": target, "groups": doomed,
                "selectors": [s for s in selectors if s["target"] in ids]}

    def revisions(self, environment, limit=50):
        return sorted(self._revisions, key=lambda r: r["occurred_at"],
                      reverse=True)[:limit]

    # ------------------------------------------------------------- writing

    def _commit(self, kind, target, actor, reason):
        groups, selectors = self._shape()
        findings = validate(groups, selectors)
        errors = blocking(findings)
        if errors:
            raise ChangeRejected(errors)
        revision_id = next(self._ids)
        self._revisions.append({
            "id": revision_id, "actor": actor, "reason": reason, "kind": kind,
            "target": str(target), "occurred_at": _now()})
        return ChangeResult({}, {"groups": groups, "selectors": selectors},
                            [f for f in findings if not f.blocking], revision_id)

    def update_group(self, environment, row_id, changes, actor, reason, request_id,
                     group_provider_configured=False):
        before = copy.deepcopy(self.groups)
        row = next((g for g in self.groups if str(g["row_id"]) == str(row_id)), None)
        if row is None:
            raise ChangeRejected([])
        for key, value in changes.items():
            if key in row or key in ("name", "scheduling_policy"):
                row[key] = value
        row["path"] = row["path"][:-1] + [row["name"]]
        try:
            return self._commit("group_update", ".".join(row["path"]), actor, reason)
        except ChangeRejected:
            self.groups = before
            raise

    def create_group(self, environment, name, parent_row_id, values, actor, reason,
                     request_id, group_provider_configured=False):
        before = copy.deepcopy(self.groups)
        parent = next((g for g in self.groups
                       if str(g["row_id"]) == str(parent_row_id)), None)
        path = (parent["path"] if parent else []) + [name]
        self.groups.append({
            "row_id": next(self._ids), "name": name, "path": path,
            "parent": parent_row_id,
            "soft_memory_limit": values.get("soft_memory_limit"),
            "hard_concurrency_limit": values.get("hard_concurrency_limit"),
            "max_queued": values.get("max_queued"),
            "jmx_export": bool(values.get("jmx_export")),
            "scheduling_policy": None})
        try:
            return self._commit("group_create", name, actor, reason)
        except ChangeRejected:
            self.groups = before
            raise

    def delete_group(self, environment, row_id, actor, reason, request_id,
                     group_provider_configured=False):
        before_groups = copy.deepcopy(self.groups)
        before_selectors = copy.deepcopy(self.selectors)
        target = next((g for g in self.groups if str(g["row_id"]) == str(row_id)), None)
        if target is None:
            raise ChangeRejected([])
        prefix = target["path"]
        doomed = {g["row_id"] for g in self.groups
                  if g["path"][:len(prefix)] == prefix}
        # The cascade, by hand: the real schema does this with two foreign keys.
        self.groups = [g for g in self.groups if g["row_id"] not in doomed]
        self.selectors = [s for s in self.selectors if s["target_row_id"] not in doomed]
        try:
            return self._commit("group_delete", row_id, actor, reason)
        except ChangeRejected:
            self.groups = before_groups
            self.selectors = before_selectors
            raise

    def create_selector(self, environment, target_row_id, priority, matchers, actor,
                        reason, request_id, group_provider_configured=False):
        before = copy.deepcopy(self.selectors)
        self.selectors.append({"id": next(self._ids), "priority": priority,
                               "target_row_id": target_row_id,
                               "matchers": dict(matchers or {})})
        try:
            return self._commit("selector_create", target_row_id, actor, reason)
        except ChangeRejected:
            self.selectors = before
            raise

    def delete_selector(self, environment, selector_id, actor, reason, request_id,
                        group_provider_configured=False):
        before = copy.deepcopy(self.selectors)
        self.selectors = [s for s in self.selectors
                          if str(s["id"]) != str(selector_id)]
        try:
            return self._commit("selector_delete", selector_id, actor, reason)
        except ChangeRejected:
            self.selectors = before
            raise

    def revert(self, environment, revision_id, actor, reason, request_id,
               group_provider_configured=False):
        return self._commit("revert", revision_id, actor, reason)
