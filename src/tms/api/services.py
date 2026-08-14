"""Endpoint logic, independent of the web framework.

FastAPI wiring lives in main.py and stays thin. Everything that can go wrong -
permissions, staleness, audit enforcement, upstream failures - is decided here,
where it can be tested without an HTTP server.

Reads come from collector snapshots, never from a live coordinator call. That is
what keeps the API horizontally scalable without multiplying load on Trino
(ARCHITECTURE.md principle A3). The single exception is query detail, which a
user opens deliberately.

Python 3.9 compatible.
"""

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from tms.api.deeplinks import build_grafana_url, build_log_url, build_query_history_url
from tms.api.errors import (
    AuditUnavailableError,
    Forbidden,
    InvalidRequest,
    NotFound,
    ReasonRequiredError,
    UpstreamUnavailable,
)
from tms.api.permissions import (
    EXPORT_AUDIT,
    KILL_QUERY,
    MANAGE_HEALTH,
    VIEW_AUDIT,
    VIEW_HEALTH,
    VIEW_QUERIES,
    Principal,
)
from tms.clients.errors import TrinoClientError, TrinoNotFound
from tms.clients.trino import build_kill_message
from tms.collector.resourcegroups import reconcile
from tms.collector.snapshot import (
    GATEWAY_SCOPE,
    KIND_GATEWAY,
    KIND_HEALTH,
    KIND_QUERIES,
    KIND_RESOURCE_GROUPS,
    Snapshot,
    utcnow,
)
from tms.core.audit import (
    ACTION_AUDIT_EXPORT,
    ACTION_HEALTH_ROLLUP_TOGGLE,
    ACTION_HEALTH_TEST_TOGGLE,
    ACTION_HEALTH_THRESHOLD_CHANGE,
    ACTION_QUERY_KILL,
    TARGET_CLUSTER,
    TARGET_HEALTH_TEST,
    TARGET_QUERY,
    AuditGuard,
    AuditUnavailable,
    ReasonRequired,
)

log = logging.getLogger(__name__)

MAX_PAGE_SIZE = 500


def summarise(queries: List[Dict[str, Any]]) -> Dict[str, int]:
    """The same counts the collector writes, over an already-narrowed list.

    Kept identical in shape to the collector's summary so the screen cannot
    tell which one it is looking at.
    """
    from tms.collector.poller import QUEUED_STATES, RUNNING_STATES

    return {
        "running": sum(1 for q in queries if q.get("state") in RUNNING_STATES),
        "queued": sum(1 for q in queries if q.get("state") in QUEUED_STATES),
        "long_running": sum(1 for q in queries if q.get("long_running")),
        "total": len(queries),
    }


def in_resource_group(query: Dict[str, Any], group_id: str) -> bool:
    """Is this query in `group_id`, or in a group beneath it?

    Trino reports the group as a path array (`["global", "adhoc"]`); the
    workload screen names it by the dotted path. Matching has to be on the
    whole path:

    * A bare segment test (`"adhoc" in path`) matches `global.adhoc` *and*
      `etl.adhoc` - two different groups, possibly with different limits. An
      operator clicking one group and being shown another's queries would draw
      the wrong conclusion about which limit is biting.
    * Subtree rather than exact match, because a parent's queries really are
      the ones in its children - Trino admits queries to leaf groups.
    """
    path = query.get("resource_group_id") or []
    if not isinstance(path, (list, tuple)) or not group_id:
        return False
    dotted = ".".join(str(part) for part in path)
    return dotted == group_id or dotted.startswith(group_id + ".")


def require(principal: Principal, capability: str) -> None:
    if not principal.can(capability):
        raise Forbidden(
            "Your role does not include {} (roles: {}).".format(
                capability, ", ".join(principal.roles) or "none")
        )


def envelope(snapshot: Optional[Snapshot], data: Any, stale_threshold: float) -> Dict[str, Any]:
    """Wrap a read response with freshness metadata.

    The server decides staleness rather than shipping a timestamp and hoping the
    client compares it correctly. A client that forgets renders stale data as
    current, which is the failure this whole design avoids.
    """
    if snapshot is None:
        return {"collected_at": None, "stale": True, "data": data}
    now = utcnow()
    return {
        "collected_at": snapshot.collected_at.isoformat(),
        "stale": snapshot.is_stale(now, stale_threshold),
        "data": data,
    }


class TmsService:
    def __init__(
        self,
        config,
        repository,
        audit_guard: AuditGuard,
        audit_repository,
        trino_clients: Dict[str, Any],
        clock: Optional[Callable[[], datetime]] = None,
        config_store=None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.audit = audit_guard
        self.audit_repository = audit_repository
        self.trino_clients = trino_clients
        self._clock = clock or utcnow
        # Trino's resource group tables (D-010). None while Trino still uses
        # the file manager - the screen then says so rather than showing an
        # empty tree that looks like "nothing is configured".
        self.config_store = config_store

    @property
    def _stale_threshold(self) -> float:
        return self.config.collector.stale_threshold_seconds

    def _cluster_or_404(self, cluster: str):
        try:
            return self.config.cluster(cluster)
        except KeyError:
            raise NotFound("Unknown cluster: {}".format(cluster))

    def _client_or_503(self, cluster: str):
        client = self.trino_clients.get(cluster)
        if client is None:
            raise UpstreamUnavailable("No client configured for cluster {}".format(cluster))
        return client

    # ------------------------------------------------------------ FR-PORTAL

    def me(self, principal: Principal) -> Dict[str, Any]:
        return {
            "user": principal.username,
            "roles": principal.roles,
            "capabilities": principal.capabilities,
        }

    def links(self, principal: Principal) -> Dict[str, Any]:
        """Link hub. Entries with no configured URL are omitted entirely."""
        deeplinks = self.config.deeplinks
        candidates = [
            ("grafana", "Grafana", build_grafana_url(deeplinks.grafana_cluster_dashboard, "")),
            ("superset", "Superset", deeplinks.superset_url),
            ("query_history", "Query History", deeplinks.query_history_home_url),
        ]
        if self.config.gateway.enabled and self.config.gateway.base_url:
            candidates.append(("gateway_ui", "Trino Gateway", self.config.gateway.base_url))
        for cluster in self.config.clusters:
            if cluster.trino_ui_url:
                candidates.append(
                    ("trino_ui_" + cluster.name, "Trino UI ({})".format(cluster.name), cluster.trino_ui_url)
                )
        return {
            "links": [
                {"id": link_id, "label": label, "url": url}
                for link_id, label, url in candidates
                if url
            ]
        }

    # -------------------------------------------------------- FR-QUERY-LIVE

    def list_queries(
        self,
        principal: Principal,
        cluster: str,
        state: Optional[List[str]] = None,
        user: Optional[str] = None,
        min_elapsed_seconds: Optional[float] = None,
        resource_group: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        require(principal, VIEW_QUERIES)
        self._cluster_or_404(cluster)
        snapshot = self.repository.load(cluster, KIND_QUERIES)
        if snapshot is None:
            return envelope(None, {"summary": {}, "queries": [], "unavailable_reason": "No data collected yet."}, self._stale_threshold)

        if not snapshot.trustworthy:
            # An empty list under a denied `queries` rule is indistinguishable
            # from an idle cluster, so refuse to present it as data.
            return envelope(
                snapshot,
                {
                    "summary": {},
                    "queries": [],
                    "unavailable_reason": snapshot.collection_error,
                    "advice": snapshot.advice,
                },
                self._stale_threshold,
            )

        queries = list(snapshot.payload.get("queries") or [])
        # Narrow by everything except state first. The state chips *are* the
        # state filter and their counts are the summary, so they have to count
        # within whatever else is applied - otherwise arriving from the
        # workload screen shows "All 47" above three rows, and the operator
        # reads the cluster as far busier than what they are looking at.
        if user:
            queries = [q for q in queries if q.get("user") == user]
        if resource_group:
            queries = [q for q in queries if in_resource_group(q, resource_group)]
        if min_elapsed_seconds is not None:
            floor_ms = min_elapsed_seconds * 1000.0
            queries = [q for q in queries if (q.get("elapsed_ms") or 0) >= floor_ms]

        scoped = queries
        if state:
            wanted = set(state)
            queries = [q for q in queries if q.get("state") in wanted]

        scoped_is_everything = not (user or resource_group or min_elapsed_seconds)
        limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        truncated = len(queries) > limit
        queries = queries[:limit]

        for query in queries:
            query["links"] = self._query_links(cluster, query)

        return envelope(
            snapshot,
            {
                "summary": (snapshot.payload.get("summary") or {}) if scoped_is_everything
                           else summarise(scoped),
                "queries": queries,
                "truncated": truncated,
            },
            self._stale_threshold,
        )

    def list_queries_all(
        self,
        principal: Principal,
        state: Optional[List[str]] = None,
        user: Optional[str] = None,
        min_elapsed_seconds: Optional[float] = None,
        resource_group: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """Every cluster's live queries in one list (FR-QL-01, All view).

        Fans out over the per-cluster snapshots rather than adding a second
        source of truth. Two rules make the merged view honest:

        * Freshness is the *oldest* contributing snapshot. A merged list is only
          as current as its worst source, and showing the newest would flatter it.
        * One cluster degrading degrades alone. Its rows drop out and it is named
          in `clusters` with its reason, while every other cluster keeps
          rendering — a single permission failure must not blank the page.
        """
        require(principal, VIEW_QUERIES)

        merged: List[Dict[str, Any]] = []
        per_cluster: List[Dict[str, Any]] = []
        summary = {"running": 0, "queued": 0, "long_running": 0, "total": 0}
        oldest: Optional[Snapshot] = None
        any_stale = False
        now = self._clock()

        for cluster in self.config.clusters:
            snapshot = self.repository.load(cluster.name, KIND_QUERIES)
            stale = True
            if snapshot is not None:
                stale = snapshot.is_stale(now, self._stale_threshold)
                if oldest is None or snapshot.collected_at < oldest.collected_at:
                    oldest = snapshot
            any_stale = any_stale or stale

            entry = {
                "name": cluster.name,
                "stale": stale,
                "collected_at": snapshot.collected_at.isoformat() if snapshot else None,
                "unavailable_reason": None,
                "advice": None,
            }

            if snapshot is None:
                entry["unavailable_reason"] = "No data collected yet."
            elif not snapshot.trustworthy:
                entry["unavailable_reason"] = snapshot.collection_error
                entry["advice"] = snapshot.advice
            else:
                for raw in snapshot.payload.get("queries") or []:
                    row = dict(raw)
                    row["cluster"] = cluster.name
                    row["links"] = self._query_links(cluster.name, row)
                    merged.append(row)
                for key in summary:
                    summary[key] += int((snapshot.payload.get("summary") or {}).get(key, 0))
            per_cluster.append(entry)

        merged = self._filter_queries(
            merged, state, user, min_elapsed_seconds, resource_group
        )
        # Worst first: the query an operator is looking for during an incident is
        # the one that has been running longest.
        merged.sort(key=lambda q: q.get("elapsed_ms") or 0, reverse=True)

        limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        truncated = len(merged) > limit

        return {
            "collected_at": oldest.collected_at.isoformat() if oldest else None,
            "stale": any_stale,
            "data": {
                "summary": summary,
                "queries": merged[:limit],
                "truncated": truncated,
                "clusters": per_cluster,
            },
        }

    @staticmethod
    def _filter_queries(
        queries: List[Dict[str, Any]],
        state: Optional[List[str]],
        user: Optional[str],
        min_elapsed_seconds: Optional[float],
        resource_group: Optional[str],
    ) -> List[Dict[str, Any]]:
        if state:
            wanted = set(state)
            queries = [q for q in queries if q.get("state") in wanted]
        if user:
            needle = user.lower()
            queries = [q for q in queries if needle in str(q.get("user") or "").lower()]
        if resource_group:
            queries = [
                q for q in queries if resource_group in (q.get("resource_group_id") or [])
            ]
        if min_elapsed_seconds is not None:
            floor_ms = min_elapsed_seconds * 1000.0
            queries = [q for q in queries if (q.get("elapsed_ms") or 0) >= floor_ms]
        return queries

    def _query_links(self, cluster: str, query: Dict[str, Any]) -> Dict[str, str]:
        deeplinks = self.config.deeplinks
        links: Dict[str, str] = {}
        log_url = build_log_url(
            deeplinks.log_template,
            query_id=query.get("query_id"),
            cluster=cluster,
            padding_seconds=deeplinks.log_padding_seconds,
        )
        if log_url:
            links["logs"] = log_url
        history_url = build_query_history_url(
            deeplinks.query_history_url_template, query.get("query_id") or ""
        )
        if history_url:
            links["history"] = history_url
        return links

    def get_query(self, principal: Principal, cluster: str, query_id: str) -> Dict[str, Any]:
        """Full detail including complete SQL.

        The only read that calls a coordinator directly. It happens when a user
        opens a query, not on a timer, so it does not affect the polling budget.
        """
        require(principal, VIEW_QUERIES)
        self._cluster_or_404(cluster)
        client = self._client_or_503(cluster)
        try:
            detail = client.get_query(query_id)
        except TrinoNotFound as exc:
            raise NotFound("Query not found: {}".format(query_id), advice=exc.advice)
        except TrinoClientError as exc:
            raise UpstreamUnavailable(str(exc), advice=exc.advice)
        return {"collected_at": self._clock().isoformat(), "stale": False, "data": detail}

    def kill_query(
        self,
        principal: Principal,
        cluster: str,
        query_id: str,
        reason: Optional[str],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """FR-QL-04. Permission, then audit, then the action - in that order."""
        self._cluster_or_404(cluster)

        if not principal.can(KILL_QUERY):
            # AU5: record the refusal. "Why did nothing happen?" is auditable.
            self.audit.record_refusal(
                actor=principal.username,
                roles=principal.roles,
                action_type=ACTION_QUERY_KILL,
                target_kind=TARGET_QUERY,
                target_id=query_id,
                target_cluster=cluster,
                reason=reason,
                error_message="403: {} lacks kill_query".format(principal.username),
                actor_ip=principal.ip,
            )
            raise Forbidden("You do not have permission to kill queries.")

        client = self._client_or_503(cluster)
        try:
            with self.audit.action(
                actor=principal.username,
                roles=principal.roles,
                action_type=ACTION_QUERY_KILL,
                target_kind=TARGET_QUERY,
                target_id=query_id,
                target_cluster=cluster,
                reason=reason,
                actor_ip=principal.ip,
                request_id=request_id,
            ) as audited:
                message = build_kill_message(
                    principal.username, audited.record.reason, audited.request_id
                )
                client.kill_query(query_id, message)
                audited.details["cluster"] = cluster
                return {"killed": True, "request_id": audited.request_id}
        except ReasonRequired as exc:
            raise ReasonRequiredError(str(exc))
        except AuditUnavailable as exc:
            # Nothing was attempted against the cluster.
            raise AuditUnavailableError(str(exc))
        except TrinoNotFound as exc:
            raise NotFound("Query not found: {}".format(query_id), advice=exc.advice)
        except TrinoClientError as exc:
            raise UpstreamUnavailable(str(exc), advice=exc.advice)

    # ---------------------------------------------------- FR-CLUSTER-HEALTH

    def list_clusters(self, principal: Principal) -> Dict[str, Any]:
        require(principal, VIEW_HEALTH)
        now = self._clock()
        rows = []
        oldest: Optional[Snapshot] = None
        for cluster in self.config.clusters:
            snapshot = self.repository.load(cluster.name, KIND_HEALTH)
            if snapshot is not None and (
                oldest is None or snapshot.collected_at < oldest.collected_at
            ):
                oldest = snapshot
            payload = (snapshot.payload if snapshot else {}) or {}
            tests = payload.get("tests") or []
            rows.append(
                {
                    "name": cluster.name,
                    "rollup_state": payload.get("rollup_state", "UNKNOWN"),
                    "bad": sum(1 for t in tests if t.get("state") == "BAD"),
                    "concerning": sum(1 for t in tests if t.get("state") == "CONCERNING"),
                    "unknown": sum(1 for t in tests if t.get("state") == "UNKNOWN"),
                    "stale": snapshot.is_stale(now, self._stale_threshold)
                    if snapshot
                    else True,
                }
            )
        return envelope(oldest, rows, self._stale_threshold)

    def get_gateway(self, principal: Principal) -> Dict[str, Any]:
        """Gateway backends joined to what TMS monitors (FR-GW-01/02).

        Fleet-level: there is one Gateway deployment behind a load balancer, so
        this takes no cluster argument.
        """
        require(principal, VIEW_HEALTH)
        enabled = self.config.gateway.enabled
        snapshot = self.repository.load(GATEWAY_SCOPE, KIND_GATEWAY)
        if snapshot is None:
            return envelope(
                None,
                {
                    "enabled": enabled, "backends": [], "groups": [],
                    "unmonitored_backends": [], "unrouted_clusters": [],
                    "routing_rules": None, "live": None,
                    "unavailable_reason": (
                        None if enabled else
                        "Gateway integration is off (gateway.enabled)."
                    ),
                },
                self._stale_threshold,
            )
        payload = dict(snapshot.payload or {})
        payload["enabled"] = enabled
        if snapshot.collection_error:
            payload["unavailable_reason"] = snapshot.collection_error
            payload["advice"] = snapshot.advice
        return envelope(snapshot, payload, self._stale_threshold)

    def get_workload(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        """Resource group tree for one cluster (FR-WORKLOAD).

        `enabled` distinguishes "collection is switched off" from "nothing was
        found". They look identical in the data - an empty tree either way -
        but the first is a configuration choice and the second may be a missing
        `jmxExport`. Telling an operator to check their config when the feature
        is simply off would waste their time.
        """
        require(principal, VIEW_HEALTH)
        self._cluster_or_404(cluster)
        enabled = self.config.workload.enabled
        snapshot = self.repository.load(cluster, KIND_RESOURCE_GROUPS)
        if snapshot is None:
            return envelope(
                None,
                {
                    "tree": [], "groups": [], "summary": {}, "complete": False,
                    "enabled": enabled,
                    "unavailable_reason": (
                        None if enabled else
                        "Resource group collection is off (workload.enabled)."
                    ),
                },
                self._stale_threshold,
            )
        payload = dict(snapshot.payload or {})
        payload["enabled"] = enabled
        if snapshot.collection_error:
            payload["unavailable_reason"] = snapshot.collection_error
            payload["advice"] = snapshot.advice
        self._add_queue_age(cluster, payload)
        return envelope(snapshot, payload, self._stale_threshold)

    def get_resource_group_config(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        """The configured resource group tree, next to what is running (FR-WL-07).

        Reads the store directly rather than a collector snapshot. Configuration
        changes when a person changes it, not on a timer, and polling it would
        add a query per interval to answer a question whose answer is almost
        always the same as last time.

        The JMX side does come from a snapshot, and may be missing - workload
        collection is off by default. The two are kept distinct in the payload
        so the screen can say which half it has.
        """
        require(principal, VIEW_HEALTH)
        cluster_config = self._cluster_or_404(cluster)

        if self.config_store is None:
            return {
                "data": {
                    "enabled": False, "rows": [], "unmanaged": [], "selectors": [],
                    "unavailable_reason": (
                        "TMS is not reading Trino's resource group store "
                        "(resource_groups.enabled). Trino is presumably still "
                        "using the file configuration manager, which TMS cannot "
                        "read."),
                },
            }

        configured = self.config_store.load_configured(cluster_config.node_environment)

        snapshot = self.repository.load(cluster, KIND_RESOURCE_GROUPS)
        live_available = bool(self.config.workload.enabled and snapshot is not None)
        live = ((snapshot.payload or {}).get("groups") or []) if snapshot else []

        rows, unmanaged = reconcile(configured.groups, live, live_available=live_available)
        return {
            "data": {
                "enabled": True,
                "environment": cluster_config.node_environment,
                "rows": rows,
                "unmanaged": unmanaged,
                "selectors": configured.selectors,
                "has_catch_all": configured.catch_all is not None,
                "live_available": live_available,
                "live_reason": (
                    None if live_available else
                    "Workload collection is off (workload.enabled), so TMS "
                    "cannot say which of these groups are running."),
                "unavailable_reason": configured.error,
                "advice": configured.advice,
            },
        }

    def _add_queue_age(self, cluster: str, payload: Dict[str, Any]) -> None:
        """How long the longest-waiting query in each group has been queued.

        FR-WL-03 asked for p50/p95 queue time. Trino's resource group MBeans do
        not expose queue-time distributions at all, so DESIGN_R2 reduced the AC
        to the current queue and the age of its oldest member - which is the
        number an operator actually acts on. "12 queued" is a fact; "12 queued,
        oldest waiting 14 minutes" is a decision.

        Joined from the live query snapshot rather than collected separately:
        `queued_ms` and `resource_group_id` are already on every row, so this
        costs nothing on the coordinator. Read-side only.

        ⛔ The two snapshots are written by different polls. If the query one is
        missing or untrustworthy, every group gets None and the column renders
        blank - a queue age carried over from an unusable read is worse than no
        queue age, because it looks current.
        """
        groups = payload.get("groups") or []
        if not groups:
            return
        queries_snapshot = self.repository.load(cluster, KIND_QUERIES)
        if queries_snapshot is None or not queries_snapshot.trustworthy:
            return

        oldest: Dict[str, float] = {}
        for query in (queries_snapshot.payload or {}).get("queries") or []:
            if query.get("state") != "QUEUED":
                continue
            path = query.get("resource_group_id") or []
            if not isinstance(path, (list, tuple)) or not path:
                continue
            waited = query.get("queued_ms")
            if waited is None:
                continue
            # Credit every ancestor too: a parent group's queue really is the
            # union of its children's, and the tree is read top-down.
            for depth in range(1, len(path) + 1):
                key = ".".join(str(part) for part in path[:depth])
                if waited > oldest.get(key, -1):
                    oldest[key] = waited

        for group in groups:
            group["oldest_queued_ms"] = oldest.get(group.get("id"))
        payload["queue_age_at"] = queries_snapshot.collected_at.isoformat()

    def get_health(self, principal: Principal, cluster: str) -> Dict[str, Any]:
        require(principal, VIEW_HEALTH)
        self._cluster_or_404(cluster)
        snapshot = self.repository.load(cluster, KIND_HEALTH)
        if snapshot is None:
            return envelope(
                None,
                {"rollup_state": "UNKNOWN", "rollup_enabled": True, "tests": []},
                self._stale_threshold,
            )
        payload = dict(snapshot.payload or {})
        for test in payload.get("tests") or []:
            links = {}
            log_url = build_log_url(
                self.config.deeplinks.log_template,
                cluster=cluster,
                padding_seconds=self.config.deeplinks.log_padding_seconds,
            )
            if log_url and test.get("state") in ("BAD", "CONCERNING"):
                links["logs"] = log_url
            if links:
                test["links"] = links
        return envelope(snapshot, payload, self._stale_threshold)

    def list_health_events(
        self, principal: Principal, cluster: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Confirmed state transitions (FR-CH-07), newest first.

        Only debounced transitions reach the store, so this is an event log an
        operator can actually read rather than a spike feed.
        """
        require(principal, VIEW_HEALTH)
        self._cluster_or_404(cluster)
        reader = getattr(self.repository, "list_health_events", None)
        if reader is None:
            return []
        try:
            return reader(cluster, max(1, min(int(limit), 200)))
        except Exception:  # noqa: BLE001 - an unreadable log must not blank the page
            log.exception("failed to read health events for %s", cluster)
            return []

    def update_health_test(
        self,
        principal: Principal,
        cluster: str,
        test_id: str,
        reason: Optional[str],
        enabled: Optional[bool] = None,
        thresholds: Optional[Dict[str, float]] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """FR-CH-03 / FR-CH-05. Admin only, audited.

        Disabling a health test narrows what operators can see, so it leaves a
        trace with a mandatory reason like any other write.
        """
        self._cluster_or_404(cluster)
        if enabled is None and not thresholds:
            raise InvalidRequest("Specify either enabled or thresholds.")

        action_type = (
            ACTION_HEALTH_THRESHOLD_CHANGE if thresholds else ACTION_HEALTH_TEST_TOGGLE
        )
        if not principal.can(MANAGE_HEALTH):
            self.audit.record_refusal(
                actor=principal.username,
                roles=principal.roles,
                action_type=action_type,
                target_kind=TARGET_HEALTH_TEST,
                target_id=test_id,
                target_cluster=cluster,
                reason=reason,
                error_message="403: {} lacks manage_health".format(principal.username),
                actor_ip=principal.ip,
            )
            raise Forbidden("You do not have permission to change health tests.")

        try:
            with self.audit.action(
                actor=principal.username,
                roles=principal.roles,
                action_type=action_type,
                target_kind=TARGET_HEALTH_TEST,
                target_id=test_id,
                target_cluster=cluster,
                reason=reason,
                actor_ip=principal.ip,
                request_id=request_id,
            ) as audited:
                audited.details = {"enabled": enabled, "thresholds": thresholds}
                self.repository.save_health_override(
                    cluster=cluster,
                    test_id=test_id,
                    enabled=enabled,
                    thresholds=thresholds,
                    updated_by=principal.username,
                )
                return {"updated": True, "request_id": audited.request_id}
        except ReasonRequired as exc:
            raise ReasonRequiredError(str(exc))
        except AuditUnavailable as exc:
            raise AuditUnavailableError(str(exc))

    def update_health_rollup(
        self,
        principal: Principal,
        cluster: str,
        enabled: bool,
        reason: Optional[str],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """FR-CH-04. The roll-up can be silenced separately from its tests."""
        self._cluster_or_404(cluster)
        if not principal.can(MANAGE_HEALTH):
            self.audit.record_refusal(
                actor=principal.username,
                roles=principal.roles,
                action_type=ACTION_HEALTH_ROLLUP_TOGGLE,
                target_kind=TARGET_CLUSTER,
                target_id=cluster,
                target_cluster=cluster,
                reason=reason,
                error_message="403: {} lacks manage_health".format(principal.username),
                actor_ip=principal.ip,
            )
            raise Forbidden("You do not have permission to change the roll-up.")

        try:
            with self.audit.action(
                actor=principal.username,
                roles=principal.roles,
                action_type=ACTION_HEALTH_ROLLUP_TOGGLE,
                target_kind=TARGET_CLUSTER,
                target_id=cluster,
                target_cluster=cluster,
                reason=reason,
                actor_ip=principal.ip,
                request_id=request_id,
            ) as audited:
                audited.details = {"enabled": enabled}
                self.repository.save_health_override(
                    cluster=cluster,
                    test_id="*",
                    enabled=enabled,
                    thresholds=None,
                    updated_by=principal.username,
                )
                return {"updated": True, "request_id": audited.request_id}
        except ReasonRequired as exc:
            raise ReasonRequiredError(str(exc))
        except AuditUnavailable as exc:
            raise AuditUnavailableError(str(exc))

    # ----------------------------------------------------- FR-AUDIT-ACTION

    def search_audit(self, principal: Principal, **filters: Any) -> Dict[str, Any]:
        require(principal, VIEW_AUDIT)
        limit = max(1, min(int(filters.pop("limit", 100) or 100), MAX_PAGE_SIZE))
        records = self.audit_repository.search(limit=limit, **filters)
        return {
            "records": [self._audit_row(r) for r in records],
            "count": len(records),
        }

    def export_audit(
        self,
        principal: Principal,
        reason: Optional[str],
        request_id: Optional[str] = None,
        **filters: Any
    ) -> Dict[str, Any]:
        """FR-AA-05. Exporting the audit log is itself an audited action.

        If nobody records who pulled the whole log, it is not an audit system.
        """
        if not principal.can(EXPORT_AUDIT):
            self.audit.record_refusal(
                actor=principal.username,
                roles=principal.roles,
                action_type=ACTION_AUDIT_EXPORT,
                target_kind=TARGET_CLUSTER,
                target_id="*",
                reason=reason,
                error_message="403: {} lacks export_audit".format(principal.username),
                actor_ip=principal.ip,
            )
            raise Forbidden("You do not have permission to export the audit log.")

        try:
            with self.audit.action(
                actor=principal.username,
                roles=principal.roles,
                action_type=ACTION_AUDIT_EXPORT,
                target_kind=TARGET_CLUSTER,
                target_id="*",
                reason=reason,
                actor_ip=principal.ip,
                request_id=request_id,
            ) as audited:
                limit = max(1, min(int(filters.pop("limit", MAX_PAGE_SIZE) or MAX_PAGE_SIZE), MAX_PAGE_SIZE))
                records = self.audit_repository.search(limit=limit, **filters)
                audited.details = {"exported_rows": len(records)}
                return {
                    "rows": [self._audit_row(r) for r in records],
                    "request_id": audited.request_id,
                }
        except ReasonRequired as exc:
            raise ReasonRequiredError(str(exc))
        except AuditUnavailable as exc:
            raise AuditUnavailableError(str(exc))

    @staticmethod
    def _audit_row(record) -> Dict[str, Any]:
        return {
            "occurred_at": record.occurred_at.isoformat() if record.occurred_at else None,
            "actor": record.actor,
            "actor_roles": record.actor_roles,
            "actor_ip": record.actor_ip,
            "action_type": record.action_type,
            "target_kind": record.target_kind,
            "target_id": record.target_id,
            "target_cluster": record.target_cluster,
            "reason": record.reason,
            "outcome": record.outcome,
            "error_message": record.error_message,
            "request_id": record.request_id,
        }
