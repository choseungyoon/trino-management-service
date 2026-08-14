"""Editing resource groups through the service (FR-WL-08/09/10).

Changing a group's concurrency limit is query admission control: it reaches
every coordinator within the refresh interval, with no restart in between to act
as a gate. So it carries the same obligations as killing a query or restarting a
cluster - admin only, reason required, audited - and these tests are mostly
about those obligations rather than about SQL.

The SQL itself is covered by tests/integration/smoke_resource_groups.py against
a real PostgreSQL; a fake cannot tell you whether a CASCADE fires.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.api.errors import Forbidden, InvalidRequest, UpstreamUnavailable  # noqa: E402
from tms.api.permissions import Principal  # noqa: E402
from tms.api.services import TmsService  # noqa: E402
from tms.collector.snapshot import InMemorySnapshotRepository  # noqa: E402
from tms.core.audit import (  # noqa: E402
    ACTION_RESOURCE_GROUP_CHANGE,
    FAILURE,
    SUCCESS,
    ACTION_RESOURCE_GROUP_REVERT,
    AuditGuard,
    InMemoryAuditRepository,
)
from tms.core.config import build_config  # noqa: E402
from tms.ops.config_store import ChangeRejected, ChangeResult  # noqa: E402
from tms.ops.resource_group_rules import ERROR, WARNING, Finding  # noqa: E402

ADMIN = Principal("op", ["admin"], ip="10.0.0.9")
OPERATOR = Principal("hands", ["operator"], ip="10.0.0.9")
VIEWER = Principal("watcher", ["viewer"], ip="10.0.0.9")


class RecordingStore:
    """Records what it was asked to do; optionally refuses."""

    def __init__(self, reject=None, warnings=()):
        self.calls = []
        self._reject = reject
        self._warnings = list(warnings)

    def _record(self, name, args):
        self.calls.append((name, args))
        if self._reject is not None:
            raise ChangeRejected(self._reject)
        return ChangeResult({"groups": []}, {"groups": []}, self._warnings, 7)

    def update_group(self, env, row_id, changes, actor, reason, rid, gp=False):
        return self._record("update_group",
                            {"env": env, "row_id": row_id, "changes": changes,
                             "actor": actor, "reason": reason, "gp": gp})

    def create_group(self, env, name, parent, values, actor, reason, rid, gp=False):
        return self._record("create_group",
                            {"env": env, "name": name, "parent": parent, "gp": gp})

    def delete_group(self, env, row_id, actor, reason, rid, gp=False):
        return self._record("delete_group", {"env": env, "row_id": row_id})

    def create_selector(self, env, target, priority, matchers, actor, reason, rid,
                        gp=False):
        return self._record("create_selector",
                            {"env": env, "target": target, "priority": priority,
                             "matchers": matchers})

    def delete_selector(self, env, selector_id, actor, reason, rid, gp=False):
        return self._record("delete_selector", {"env": env, "selector_id": selector_id})

    def revert(self, env, revision_id, actor, reason, rid, gp=False):
        return self._record("revert", {"env": env, "revision_id": revision_id})

    def deletion_impact(self, env, row_id):
        return {"group": {"id": "global", "row_id": row_id}, "groups": [], "selectors": []}

    def revisions(self, env, limit=50):
        return [{"id": 1, "actor": "op", "reason": "tuning", "kind": "group_update",
                 "target": "1", "occurred_at": None}]


def build(store=None, enabled=True, node_environment="cluster1",
          group_provider=False):
    config = build_config({
        "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid",
                      "expected_workers": 11,
                      "node_environment": node_environment}],
        "trino": {"user": "u", "password": "p"},
        "database": {"url": "postgresql://u:p@h/d"},
        "resource_groups": {"enabled": enabled,
                            "group_provider_configured": group_provider},
    })
    audit = InMemoryAuditRepository()
    service = TmsService(
        config=config, repository=InMemorySnapshotRepository(),
        audit_guard=AuditGuard(audit), audit_repository=audit,
        trino_clients={}, config_store=store)
    return service, audit


class PermissionTest(unittest.TestCase):
    def test_a_viewer_cannot_edit(self):
        store = RecordingStore()
        service, _ = build(store)
        with self.assertRaises(Forbidden):
            service.update_resource_group(VIEWER, "prod-a", 1, {"max_queued": 10}, "no")
        self.assertEqual([], store.calls, "nothing reached the store")

    def test_an_operator_cannot_edit_either(self):
        """Same grade as restarting a cluster, not as killing one query."""
        store = RecordingStore()
        service, _ = build(store)
        with self.assertRaises(Forbidden):
            service.update_resource_group(OPERATOR, "prod-a", 1, {"max_queued": 10}, "no")
        self.assertEqual([], store.calls)


class ReasonAndAuditTest(unittest.TestCase):
    def test_a_blank_reason_is_refused_before_the_store_is_touched(self):
        store = RecordingStore()
        service, _ = build(store)
        with self.assertRaises(Exception):
            service.update_resource_group(ADMIN, "prod-a", 1, {"max_queued": 10}, "  ")
        self.assertEqual([], store.calls)

    def test_a_successful_change_is_audited_with_its_reason(self):
        service, audit = build(RecordingStore())
        service.update_resource_group(
            ADMIN, "prod-a", 1, {"max_queued": 200}, "queue was too short")
        record = audit.records[-1]
        self.assertEqual(ACTION_RESOURCE_GROUP_CHANGE, record.action_type)
        self.assertEqual("queue was too short", record.reason)
        self.assertEqual("op", record.actor)
        self.assertEqual(SUCCESS, record.outcome)

    def test_a_refused_change_still_leaves_an_audit_trail(self):
        """The attempt happened. A record only of successes is not a record."""
        service, audit = build(RecordingStore(
            reject=[Finding(ERROR, "V2", "global", "Concurrency limit is 0.")]))
        with self.assertRaises(InvalidRequest):
            service.update_resource_group(
                ADMIN, "prod-a", 1, {"hard_concurrency_limit": 0}, "typo")
        record = audit.records[-1]
        self.assertEqual(ACTION_RESOURCE_GROUP_CHANGE, record.action_type)
        self.assertEqual(FAILURE, record.outcome)

    def test_reverting_is_audited_as_its_own_action(self):
        """So "how often do we undo these" stays answerable."""
        service, audit = build(RecordingStore())
        service.revert_resource_group(ADMIN, "prod-a", 3, "the change made it worse")
        self.assertEqual(ACTION_RESOURCE_GROUP_REVERT, audit.records[-1].action_type)


class RoutingTest(unittest.TestCase):
    def test_the_cluster_node_environment_is_what_reaches_the_store(self):
        """Trino scopes rows by node.environment, not by the TMS cluster name."""
        store = RecordingStore()
        service, _ = build(store, node_environment="cluster2")
        service.update_resource_group(ADMIN, "prod-a", 1, {"max_queued": 10}, "why")
        self.assertEqual("cluster2", store.calls[0][1]["env"])

    def test_editing_is_refused_when_the_store_is_not_configured(self):
        service, _ = build(store=None, enabled=False)
        with self.assertRaises(UpstreamUnavailable):
            service.update_resource_group(ADMIN, "prod-a", 1, {"max_queued": 10}, "why")

    def test_editing_is_refused_when_the_cluster_has_no_node_environment(self):
        """Otherwise TMS would write rows no coordinator ever reads."""
        service, _ = build(RecordingStore(), node_environment="")
        with self.assertRaises(InvalidRequest):
            service.update_resource_group(ADMIN, "prod-a", 1, {"max_queued": 10}, "why")

    def test_whether_a_group_provider_exists_is_passed_through(self):
        """It decides whether a user_group_regex selector gets a warning."""
        store = RecordingStore()
        service, _ = build(store, group_provider=True)
        service.update_resource_group(ADMIN, "prod-a", 1, {"max_queued": 10}, "why")
        self.assertTrue(store.calls[0][1]["gp"])


class OutcomeTest(unittest.TestCase):
    def test_warnings_come_back_for_the_screen_to_show(self):
        store = RecordingStore(warnings=[
            Finding(WARNING, "W3", "global", "A scan quota queues rather than fails.")])
        service, _ = build(store)
        result = service.update_resource_group(
            ADMIN, "prod-a", 1, {"hard_physical_data_scan_limit": "10GB"}, "cap it")
        self.assertEqual(["W3"], [w["code"] for w in result["warnings"]])

    def test_a_rejection_names_every_reason_at_once(self):
        """Fixing one problem per save is how a five-minute edit becomes an hour."""
        service, _ = build(RecordingStore(reject=[
            Finding(ERROR, "V2", "global", "Max queued is required."),
            Finding(ERROR, "V10", "selectors", "No catch-all selector."),
        ]))
        with self.assertRaises(InvalidRequest) as caught:
            service.update_resource_group(ADMIN, "prod-a", 1, {"max_queued": None}, "x")
        message = str(caught.exception)
        self.assertIn("Max queued is required", message)
        self.assertIn("No catch-all selector", message)

    def test_deletion_impact_is_readable_without_edit_rights(self):
        """Seeing what a delete would do is not itself a change."""
        service, _ = build(RecordingStore())
        impact = service.resource_group_deletion_impact(VIEWER, "prod-a", 1)
        self.assertEqual("global", impact["group"]["id"])

    def test_history_is_readable_without_edit_rights(self):
        service, _ = build(RecordingStore())
        self.assertEqual(1, len(service.resource_group_revisions(VIEWER, "prod-a")))


if __name__ == "__main__":
    unittest.main()
