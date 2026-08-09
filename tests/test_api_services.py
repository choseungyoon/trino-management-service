"""Tests for the API service layer.

Written against the service objects rather than through HTTP so the rules can be
checked without a web server. The invariants worth protecting:

* Reads come from snapshots, never from a live coordinator call. Breaking this
  makes API replicas multiply the load on Trino (principle A3).
* An untrustworthy query snapshot is never served as an empty list. That is the
  silent failure H-09 exists to catch, and it must not be undone at the edge.
* A 403 records a refusal before it raises (AU5).
* Audit unavailability blocks the write and is reported distinctly from Trino
  being unreachable - same status, different remedy.
"""

import os
import sys
import unittest
from datetime import timedelta

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.api.errors import (  # noqa: E402
    AuditUnavailableError,
    Forbidden,
    InvalidRequest,
    NotFound,
    ReasonRequiredError,
    UpstreamUnavailable,
)
from tms.api.permissions import Principal  # noqa: E402
from tms.api.services import TmsService  # noqa: E402
from tms.clients.errors import TrinoForbidden, TrinoNotFound  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    KIND_HEALTH,
    KIND_QUERIES,
    KIND_RESOURCE_GROUPS,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.core.audit import (  # noqa: E402
    ACTION_QUERY_KILL,
    FAILURE,
    SUCCESS,
    AuditGuard,
    InMemoryAuditRepository,
)
from tms.core.config import build_config  # noqa: E402

VIEWER = Principal("viewer1", ["viewer"], ip="10.0.0.1")
OPERATOR = Principal("op1", ["operator"], ip="10.0.0.2")
ADMIN = Principal("admin1", ["admin"], ip="10.0.0.3")

RAW_CONFIG = {
    "clusters": [
        {"name": "prod-a", "coordinator_url": "https://a.invalid:8443", "expected_workers": 12},
        {"name": "prod-b", "coordinator_url": "https://b.invalid:8443", "expected_workers": 12},
    ],
    "trino": {"user": "tms-svc", "password": "pw"},
    "database": {"url": "postgresql://tms:pw@db.invalid/tms"},
    "deeplinks": {
        "log": {"template": "https://loki.invalid/explore?q={query}&from={from_ms}&to={to_ms}"},
        "query_history": {
            "query_url_template": "https://history.invalid/query/{query_id}",
            "home_url": "https://history.invalid/",
        },
    },
}


class FakeTrinoClient:
    def __init__(self):
        self.killed = []
        self.raise_on_kill = None
        self.raise_on_get = None
        self.detail = {"queryId": "q1", "query": "SELECT 1"}

    def kill_query(self, query_id, message):
        if self.raise_on_kill:
            raise self.raise_on_kill
        self.killed.append((query_id, message))

    def get_query(self, query_id):
        if self.raise_on_get:
            raise self.raise_on_get
        return self.detail

    def list_queries(self, states=None):  # must never be called by the API
        raise AssertionError("the API must read snapshots, not poll Trino")


def build_service(writable_audit=True, config_overrides=None):
    raw = dict(RAW_CONFIG)
    if config_overrides:
        raw.update(config_overrides)
    config = build_config(raw)
    snapshots = InMemorySnapshotRepository()
    audit_repository = InMemoryAuditRepository(writable=writable_audit)
    clients = {"prod-a": FakeTrinoClient(), "prod-b": FakeTrinoClient()}
    service = TmsService(
        config=config,
        repository=snapshots,
        audit_guard=AuditGuard(audit_repository),
        audit_repository=audit_repository,
        trino_clients=clients,
    )
    return service, snapshots, audit_repository, clients


def query_snapshot(cluster="prod-a", queries=None, error=None, age_seconds=0,
                   summary=None):
    return Snapshot(
        cluster=cluster,
        kind=KIND_QUERIES,
        collected_at=utcnow() - timedelta(seconds=age_seconds),
        payload={
            "queries": queries if queries is not None else [],
            "summary": summary if summary is not None else {
                "running": len(queries or []), "queued": 0, "total": len(queries or [])},
        },
        collection_error=error,
        advice="Check rules.json" if error else None,
    )


def sample_query(query_id="q1", user="analyst", elapsed_ms=10000, state="RUNNING", rg=None):
    return {
        "query_id": query_id,
        "state": state,
        "user": user,
        "elapsed_ms": elapsed_ms,
        "resource_group_id": rg or ["global", "bi"],
        "query_preview": "SELECT 1",
    }


class PermissionTest(unittest.TestCase):
    def test_viewer_cannot_kill(self):
        service, _, audit, clients = build_service()
        with self.assertRaises(Forbidden):
            service.kill_query(VIEWER, "prod-a", "q1", reason="why")
        self.assertEqual(clients["prod-a"].killed, [], "the kill was attempted anyway")

    def test_refusal_is_audited_before_raising(self):
        """AU5: 'why did nothing happen?' must be answerable."""
        service, _, audit, _ = build_service()
        with self.assertRaises(Forbidden):
            service.kill_query(VIEWER, "prod-a", "q1", reason="why")
        self.assertEqual(len(audit.records), 1)
        record = audit.records[0]
        self.assertEqual(record.outcome, FAILURE)
        self.assertEqual(record.actor, "viewer1")
        self.assertIn("403", record.error_message)

    def test_operator_cannot_manage_health(self):
        service, _, _, _ = build_service()
        with self.assertRaises(Forbidden):
            service.update_health_test(OPERATOR, "prod-a", "H-05", reason="r", enabled=False)

    def test_viewer_cannot_read_audit(self):
        service, _, _, _ = build_service()
        with self.assertRaises(Forbidden):
            service.search_audit(VIEWER)

    def test_operator_cannot_export_audit(self):
        service, _, _, _ = build_service()
        with self.assertRaises(Forbidden):
            service.export_audit(OPERATOR, reason="need it")

    def test_me_exposes_capabilities_for_ui_hiding(self):
        service, _, _, _ = build_service()
        self.assertIn("kill_query", service.me(OPERATOR)["capabilities"])
        self.assertNotIn("kill_query", service.me(VIEWER)["capabilities"])


class KillQueryTest(unittest.TestCase):
    def test_successful_kill_is_audited_with_the_reason(self):
        service, _, audit, clients = build_service()
        result = service.kill_query(OPERATOR, "prod-a", "q1", reason="리소스 고갈 유발")
        self.assertTrue(result["killed"])
        self.assertEqual(audit.records[0].outcome, SUCCESS)
        self.assertEqual(audit.records[0].reason, "리소스 고갈 유발")

    def test_reason_reaches_the_user_whose_query_was_killed(self):
        service, _, _, clients = build_service()
        service.kill_query(OPERATOR, "prod-a", "q1", reason="리소스 고갈")
        _query_id, message = clients["prod-a"].killed[0]
        self.assertIn("리소스 고갈", message)
        self.assertIn("actor=op1", message)

    def test_blank_reason_is_400_not_503(self):
        """A malformed request and a dead database are different problems."""
        service, _, _, _ = build_service()
        with self.assertRaises(ReasonRequiredError):
            service.kill_query(OPERATOR, "prod-a", "q1", reason="   ")

    def test_audit_unavailable_blocks_the_kill(self):
        service, _, _, clients = build_service(writable_audit=False)
        with self.assertRaises(AuditUnavailableError):
            service.kill_query(OPERATOR, "prod-a", "q1", reason="why")
        self.assertEqual(clients["prod-a"].killed, [], "kill ran without an audit record")

    def test_trino_failure_is_recorded_as_a_failed_action(self):
        service, _, audit, clients = build_service()
        clients["prod-a"].raise_on_kill = TrinoForbidden("denied")
        with self.assertRaises(UpstreamUnavailable):
            service.kill_query(OPERATOR, "prod-a", "q1", reason="why")
        self.assertEqual(audit.records[0].outcome, FAILURE)

    def test_missing_query_is_404(self):
        service, _, _, clients = build_service()
        clients["prod-a"].raise_on_kill = TrinoNotFound("gone")
        with self.assertRaises(NotFound):
            service.kill_query(OPERATOR, "prod-a", "q1", reason="why")

    def test_unknown_cluster_is_404(self):
        service, _, _, _ = build_service()
        with self.assertRaises(NotFound):
            service.kill_query(OPERATOR, "nope", "q1", reason="why")


class ListQueriesTest(unittest.TestCase):
    def test_reads_from_the_snapshot_not_from_trino(self):
        """FakeTrinoClient.list_queries raises if the API ever calls it."""
        service, snapshots, _, _ = build_service()
        snapshots.save(query_snapshot(queries=[sample_query()]))
        response = service.list_queries(VIEWER, "prod-a")
        self.assertEqual(len(response["data"]["queries"]), 1)

    def test_untrustworthy_snapshot_is_not_served_as_an_empty_list(self):
        """The whole point of H-09: do not present a filtered list as idle."""
        service, snapshots, _, _ = build_service()
        snapshots.save(
            query_snapshot(queries=[], error="query list is empty but JMX reports 7 running")
        )
        response = service.list_queries(VIEWER, "prod-a")
        self.assertIn("unavailable_reason", response["data"])
        self.assertTrue(response["data"]["advice"])
        self.assertEqual(response["data"]["summary"], {})

    def test_stale_snapshot_is_flagged_by_the_server(self):
        service, snapshots, _, _ = build_service()
        snapshots.save(query_snapshot(queries=[sample_query()], age_seconds=120))
        self.assertTrue(service.list_queries(VIEWER, "prod-a")["stale"])

    def test_fresh_snapshot_is_not_flagged(self):
        service, snapshots, _, _ = build_service()
        snapshots.save(query_snapshot(queries=[sample_query()]))
        self.assertFalse(service.list_queries(VIEWER, "prod-a")["stale"])

    def test_missing_snapshot_is_stale_with_a_reason(self):
        service, _, _, _ = build_service()
        response = service.list_queries(VIEWER, "prod-a")
        self.assertTrue(response["stale"])
        self.assertIn("unavailable_reason", response["data"])

    def test_filters(self):
        service, snapshots, _, _ = build_service()
        snapshots.save(
            query_snapshot(
                queries=[
                    sample_query("q1", user="alice", elapsed_ms=1000),
                    sample_query("q2", user="bob", elapsed_ms=600000),
                    sample_query("q3", user="alice", state="QUEUED", rg=["global", "adhoc"]),
                ]
            )
        )
        self.assertEqual(len(service.list_queries(VIEWER, "prod-a", user="alice")["data"]["queries"]), 2)
        self.assertEqual(
            len(service.list_queries(VIEWER, "prod-a", state=["QUEUED"])["data"]["queries"]), 1
        )
        self.assertEqual(
            len(service.list_queries(VIEWER, "prod-a", min_elapsed_seconds=300)["data"]["queries"]), 1
        )
        # Group filtering is on the whole dotted path, not a segment.
        self.assertEqual(
            len(service.list_queries(
                VIEWER, "prod-a", resource_group="global.adhoc")["data"]["queries"]), 1
        )

    def test_group_filter_matches_the_path_not_a_segment(self):
        """`global.adhoc` and `etl.adhoc` are different groups with possibly
        different limits. Matching the bare segment `adhoc` would show one
        group's queries under the other, and the operator would conclude the
        wrong limit was biting."""
        service, snapshots, _, _ = build_service()
        snapshots.save(query_snapshot(queries=[
            sample_query("q1", state="QUEUED", rg=["global", "adhoc"]),
            sample_query("q2", state="QUEUED", rg=["etl", "adhoc"]),
            sample_query("q3", state="QUEUED", rg=["global", "adhoc", "small"]),
        ]))

        def ids(group):
            return sorted(q["query_id"] for q in service.list_queries(
                VIEWER, "prod-a", resource_group=group)["data"]["queries"])

        self.assertEqual(["q1", "q3"], ids("global.adhoc"))
        self.assertEqual(["q2"], ids("etl.adhoc"))
        # A parent's queue really is its children's - Trino admits to leaves.
        self.assertEqual(["q1", "q3"], ids("global"))
        self.assertEqual([], ids("adhoc"), "a bare segment is not a group")

    def test_deeplinks_are_attached(self):
        service, snapshots, _, _ = build_service()
        snapshots.save(query_snapshot(queries=[sample_query("q1")]))
        links = service.list_queries(VIEWER, "prod-a")["data"]["queries"][0]["links"]
        self.assertIn("loki.invalid", links["logs"])
        self.assertIn("history.invalid/query/q1", links["history"])

    def test_no_dead_links_when_templates_are_unset(self):
        service, snapshots, _, _ = build_service(config_overrides={"deeplinks": {}})
        snapshots.save(query_snapshot(queries=[sample_query("q1")]))
        self.assertEqual(service.list_queries(VIEWER, "prod-a")["data"]["queries"][0]["links"], {})


class QueryDetailTest(unittest.TestCase):
    def test_detail_calls_the_coordinator_directly(self):
        """The one read that is allowed to: a user opened it deliberately."""
        service, _, _, clients = build_service()
        response = service.get_query(VIEWER, "prod-a", "q1")
        self.assertEqual(response["data"]["queryId"], "q1")

    def test_upstream_failure_carries_advice(self):
        service, _, _, clients = build_service()
        clients["prod-a"].raise_on_get = TrinoForbidden("denied")
        with self.assertRaises(UpstreamUnavailable) as ctx:
            service.get_query(VIEWER, "prod-a", "q1")
        self.assertIn("rules.json", ctx.exception.advice)


class HealthTest(unittest.TestCase):
    def _health_snapshot(self, cluster="prod-a", rollup="GOOD", tests=None, age=0):
        return Snapshot(
            cluster=cluster,
            kind=KIND_HEALTH,
            collected_at=utcnow() - timedelta(seconds=age),
            payload={
                "rollup_state": rollup,
                "rollup_enabled": True,
                "tests": tests or [{"id": "H-01", "state": "GOOD", "advice": ""}],
            },
        )

    def test_cluster_list_counts_states(self):
        service, snapshots, _, _ = build_service()
        snapshots.save(
            self._health_snapshot(
                tests=[
                    {"id": "H-01", "state": "GOOD"},
                    {"id": "H-03", "state": "BAD"},
                    {"id": "H-05", "state": "UNKNOWN"},
                ],
                rollup="BAD",
            )
        )
        rows = service.list_clusters(VIEWER)["data"]
        prod_a = [r for r in rows if r["name"] == "prod-a"][0]
        self.assertEqual(prod_a["rollup_state"], "BAD")
        self.assertEqual(prod_a["bad"], 1)
        self.assertEqual(prod_a["unknown"], 1)

    def test_cluster_without_a_snapshot_reports_unknown_not_good(self):
        service, _, _, _ = build_service()
        rows = service.list_clusters(VIEWER)["data"]
        self.assertTrue(all(r["rollup_state"] == "UNKNOWN" for r in rows))
        self.assertTrue(all(r["stale"] for r in rows))

    def test_admin_can_disable_a_test_and_it_is_audited(self):
        service, snapshots, audit, _ = build_service()
        result = service.update_health_test(
            ADMIN, "prod-a", "H-05", reason="오탐 조사 중", enabled=False
        )
        self.assertTrue(result["updated"])
        self.assertEqual(audit.records[0].action_type, "HEALTH_TEST_TOGGLE")
        self.assertEqual(snapshots.load_health_overrides("prod-a")["H-05"]["enabled"], False)

    def test_threshold_change_uses_its_own_action_type(self):
        service, _, audit, _ = build_service()
        service.update_health_test(
            ADMIN, "prod-a", "H-04", reason="힙 기준 상향", thresholds={"heap_used_pct_bad": 95}
        )
        self.assertEqual(audit.records[0].action_type, "HEALTH_THRESHOLD_CHANGE")

    def test_empty_patch_is_rejected(self):
        service, _, _, _ = build_service()
        with self.assertRaises(InvalidRequest):
            service.update_health_test(ADMIN, "prod-a", "H-04", reason="r")

    def test_disabling_a_test_requires_a_reason(self):
        service, _, _, _ = build_service()
        with self.assertRaises(ReasonRequiredError):
            service.update_health_test(ADMIN, "prod-a", "H-05", reason="", enabled=False)

    def test_rollup_toggle_is_separate_from_tests(self):
        service, snapshots, audit, _ = build_service()
        service.update_health_rollup(ADMIN, "prod-a", enabled=False, reason="점검 중")
        self.assertEqual(audit.records[0].action_type, "HEALTH_ROLLUP_TOGGLE")
        self.assertEqual(snapshots.load_health_overrides("prod-a")["*"]["enabled"], False)


class AuditEndpointTest(unittest.TestCase):
    def test_export_is_itself_audited(self):
        """If nobody records who pulled the log, it is not an audit system."""
        service, _, audit, _ = build_service()
        service.export_audit(ADMIN, reason="분기 감사 제출")
        actions = [r.action_type for r in audit.records]
        self.assertIn("AUDIT_EXPORT", actions)

    def test_export_requires_a_reason(self):
        service, _, _, _ = build_service()
        with self.assertRaises(ReasonRequiredError):
            service.export_audit(ADMIN, reason=None)

    def test_search_returns_previous_actions(self):
        service, _, _, _ = build_service()
        service.kill_query(OPERATOR, "prod-a", "q1", reason="정리")
        rows = service.search_audit(OPERATOR)["records"]
        self.assertEqual(rows[0]["action_type"], ACTION_QUERY_KILL)
        self.assertEqual(rows[0]["actor"], "op1")


class LinkHubTest(unittest.TestCase):
    def test_unset_links_are_omitted(self):
        service, _, _, _ = build_service(config_overrides={"deeplinks": {}})
        self.assertEqual(service.links(VIEWER)["links"], [])

    def test_query_history_link_is_present_when_configured(self):
        """The only R1 route to completed queries (D-001)."""
        service, _, _, _ = build_service()
        ids = [link["id"] for link in service.links(VIEWER)["links"]]
        self.assertIn("query_history", ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class WorkloadJoinTest(unittest.TestCase):
    """FR-WL-03 (reduced) and the summary the chips are built from."""

    def _service(self, groups, queries):
        service, snapshots, _, _ = build_service()
        snapshots.save(Snapshot("prod-a", KIND_RESOURCE_GROUPS, utcnow(), payload={
            "tree": [], "groups": groups, "summary": {}, "complete": False,
        }))
        if queries is not None:
            snapshots.save(query_snapshot(queries=queries))
        return service

    def test_queue_age_is_the_oldest_queued_query_in_the_group(self):
        """FR-WL-03 asked for p50/p95, which Trino's group MBeans do not expose
        at all. The reduced AC is the number an operator acts on: "12 queued"
        is a fact, "oldest waiting 14 minutes" is a decision."""
        service = self._service(
            [{"id": "global"}, {"id": "global.adhoc"}],
            [sample_query("q1", state="QUEUED", rg=["global", "adhoc"]),
             sample_query("q2", state="QUEUED", rg=["global", "adhoc"])])
        service.repository.load("prod-a", KIND_QUERIES).payload["queries"][0]["queued_ms"] = 5000.0
        service.repository.load("prod-a", KIND_QUERIES).payload["queries"][1]["queued_ms"] = 90000.0

        groups = {g["id"]: g for g in
                  service.get_workload(VIEWER, "prod-a")["data"]["groups"]}
        self.assertEqual(90000.0, groups["global.adhoc"]["oldest_queued_ms"])
        # A parent's queue is the union of its children's.
        self.assertEqual(90000.0, groups["global"]["oldest_queued_ms"])

    def test_running_queries_do_not_count_as_queue_age(self):
        service = self._service(
            [{"id": "global.adhoc"}],
            [sample_query("q1", state="RUNNING", rg=["global", "adhoc"])])
        groups = service.get_workload(VIEWER, "prod-a")["data"]["groups"]
        self.assertIsNone(groups[0]["oldest_queued_ms"])

    def test_no_query_snapshot_means_no_queue_age_rather_than_a_stale_one(self):
        """The two snapshots come from different polls. A queue age carried
        over from an unusable read looks current, which is worse than blank."""
        service = self._service([{"id": "global.adhoc"}], None)
        data = service.get_workload(VIEWER, "prod-a")["data"]
        self.assertNotIn("oldest_queued_ms", data["groups"][0])
        self.assertIsNone(data.get("queue_age_at"))

    def test_the_summary_counts_within_the_applied_filters(self):
        """Arriving from the workload screen must not show "All 47" above three
        rows - the operator would read the cluster as far busier than what they
        are looking at."""
        service, snapshots, _, _ = build_service()
        snapshots.save(query_snapshot(queries=[
            sample_query("q1", state="RUNNING", rg=["global", "adhoc"]),
            sample_query("q2", state="QUEUED", rg=["global", "adhoc"]),
            sample_query("q3", state="RUNNING", rg=["etl", "nightly"]),
            sample_query("q4", state="RUNNING", rg=["etl", "nightly"]),
        ]))
        summary = service.list_queries(
            VIEWER, "prod-a", resource_group="global.adhoc")["data"]["summary"]
        self.assertEqual({"running": 1, "queued": 1, "long_running": 0, "total": 2}, summary)

    def test_the_state_chips_still_count_across_states(self):
        """The chips are the state filter, so they must not count within it -
        otherwise selecting Running shows "Queued 0" and hides the queue."""
        service, snapshots, _, _ = build_service()
        snapshots.save(query_snapshot(queries=[
            sample_query("q1", state="RUNNING", rg=["global", "adhoc"]),
            sample_query("q2", state="QUEUED", rg=["global", "adhoc"]),
        ]))
        summary = service.list_queries(
            VIEWER, "prod-a", resource_group="global.adhoc",
            state=["RUNNING"])["data"]["summary"]
        self.assertEqual(1, summary["queued"], "the queue is still reported")
        self.assertEqual(2, summary["total"])

    def test_an_unfiltered_summary_is_the_collectors_own(self):
        """No recomputation when nothing is narrowed: the collector's summary is
        derived from the full result, before any page limit."""
        service, snapshots, _, _ = build_service()
        snapshots.save(query_snapshot(
            queries=[sample_query("q1")],
            summary={"running": 41, "queued": 5, "long_running": 2, "total": 46}))
        self.assertEqual(
            46, service.list_queries(VIEWER, "prod-a")["data"]["summary"]["total"])
