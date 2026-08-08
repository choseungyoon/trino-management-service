"""Tests for driving the restart sequence (FR-CO-02).

The sequence's ordering rules are tested in test_safe_sequence. These are about
what the service does around them: refusing to start what it cannot record,
re-observing before every decision, and restoring traffic on every exit path.
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
from tms.collector.snapshot import (  # noqa: E402
    GATEWAY_SCOPE,
    KIND_GATEWAY,
    KIND_HEALTH,
    KIND_QUERIES,
    InMemorySnapshotRepository,
    Snapshot,
    utcnow,
)
from tms.core.audit import AuditGuard, InMemoryAuditRepository  # noqa: E402
from tms.core.config import build_config  # noqa: E402
from tms.ops.executor import ManualExecutor  # noqa: E402
from tms.ops.repository import InMemorySequenceRepository  # noqa: E402
from tms.ops.sequence import ABORTED, DRAINED, DRAINING, RESTARTING  # noqa: E402
from tms.ops.service import RestartService  # noqa: E402

ADMIN = Principal("op", ["admin"], ip="10.0.0.9")
VIEWER = Principal("watcher", ["viewer"], ip="10.0.0.9")


class FakeGateway:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def set_active(self, name, active):
        if self.fail:
            raise RuntimeError("gateway unreachable")
        self.calls.append((name, active))


def build(running=0, health="GOOD", gateway=None, backend_matched=True):
    config = build_config({
        "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid:8443",
                      "expected_workers": 12}],
        "trino": {"user": "u", "password": "p"},
        "database": {"url": "postgresql://u:p@h/d"},
    })
    snapshots = InMemorySnapshotRepository()
    snapshots.save(Snapshot("prod-a", KIND_QUERIES, utcnow(),
                            payload={"summary": {"running": running, "queued": 0}}))
    snapshots.save(Snapshot("prod-a", KIND_HEALTH, utcnow(),
                            payload={"rollup_state": health}))
    snapshots.save(Snapshot(GATEWAY_SCOPE, KIND_GATEWAY, utcnow(), payload={
        "backends": [{"name": "backend-a",
                      "cluster": "prod-a" if backend_matched else None}]}))
    gw = gateway if gateway is not None else FakeGateway()
    service = RestartService(
        config=config, repository=InMemorySequenceRepository(), snapshots=snapshots,
        gateway_client=gw, audit_guard=AuditGuard(InMemoryAuditRepository()),
        executor=ManualExecutor())
    return service, gw, snapshots


class PermissionTest(unittest.TestCase):
    def test_a_viewer_cannot_restart_a_cluster(self):
        service, gw, _ = build()
        with self.assertRaises(Forbidden):
            service.start(VIEWER, "prod-a", "nope")
        self.assertEqual([], gw.calls, "nothing was deactivated")

    def test_a_reason_is_required(self):
        service, gw, _ = build()
        with self.assertRaises(InvalidRequest):
            service.start(ADMIN, "prod-a", "   ")
        self.assertEqual([], gw.calls)


class StartTest(unittest.TestCase):
    def test_start_deactivates_the_matched_backend(self):
        """Not the cluster name — names drift between the two sides."""
        service, gw, _ = build()
        service.start(ADMIN, "prod-a", "rolling restart")
        self.assertEqual([("backend-a", False)], gw.calls)

    def test_it_refuses_when_no_backend_is_matched(self):
        """Guessing which backend to deactivate could stop the wrong cluster."""
        service, gw, _ = build(backend_matched=False)
        with self.assertRaises(UpstreamUnavailable):
            service.start(ADMIN, "prod-a", "rolling restart")

    def test_a_failed_deactivate_leaves_no_phantom_sequence(self):
        """Traffic was never stopped, so nothing is holding it back."""
        service, gw, _ = build(gateway=FakeGateway(fail=True))
        with self.assertRaises(Exception):
            service.start(ADMIN, "prod-a", "rolling restart")
        self.assertEqual([], service.active(), "no sequence left holding traffic")

    def test_two_restarts_of_one_cluster_are_refused(self):
        service, _, _ = build(running=2)
        service.start(ADMIN, "prod-a", "first")
        with self.assertRaises(InvalidRequest):
            service.start(ADMIN, "prod-a", "second")

    def test_every_step_is_audited_with_the_reason(self):
        service, _, _ = build()
        service.start(ADMIN, "prod-a", "rolling config change")
        records = service.audit.repository.records
        self.assertTrue(records)
        self.assertEqual("CLUSTER_RESTART", records[0].action_type)
        self.assertIn("rolling config change", records[0].reason)


class DrainTest(unittest.TestCase):
    def test_a_busy_cluster_stays_in_draining(self):
        service, _, _ = build(running=4)
        state = service.start(ADMIN, "prod-a", "rolling restart")
        self.assertEqual(DRAINING, state["state"])
        self.assertEqual(4, state["running_queries"])

    def test_restart_is_refused_while_queries_run(self):
        service, _, _ = build(running=4)
        started = service.start(ADMIN, "prod-a", "rolling restart")
        with self.assertRaises(InvalidRequest) as caught:
            service.restart(ADMIN, started["id"])
        self.assertIn("still running", str(caught.exception))

    def test_an_empty_cluster_reaches_drained(self):
        service, _, _ = build(running=0)
        self.assertEqual(DRAINED, service.start(ADMIN, "prod-a", "restart")["state"])

    def test_it_re_observes_rather_than_trusting_the_stored_count(self):
        """A count from a minute ago is not evidence a restart is safe now."""
        service, _, snapshots = build(running=0)
        started = service.start(ADMIN, "prod-a", "restart")
        snapshots.save(Snapshot("prod-a", KIND_QUERIES, utcnow(),
                                payload={"summary": {"running": 3, "queued": 0}}))
        with self.assertRaises(InvalidRequest):
            service.restart(ADMIN, started["id"])


class CompletionTest(unittest.TestCase):
    def _to_verifying(self, service, sequence_id):
        service.restart(ADMIN, sequence_id)
        return service.mark_restarted(ADMIN, sequence_id)

    def test_traffic_returns_only_after_health_is_good(self):
        service, gw, _ = build(running=0, health="GOOD")
        started = service.start(ADMIN, "prod-a", "restart")
        self._to_verifying(service, started["id"])
        result = service.complete(ADMIN, started["id"])
        self.assertEqual("COMPLETED", result["state"])
        self.assertEqual(("backend-a", True), gw.calls[-1])

    def test_bad_health_blocks_reactivation(self):
        service, gw, snapshots = build(running=0, health="GOOD")
        started = service.start(ADMIN, "prod-a", "restart")
        self._to_verifying(service, started["id"])
        snapshots.save(Snapshot("prod-a", KIND_HEALTH, utcnow(),
                                payload={"rollup_state": "BAD"}))
        with self.assertRaises(InvalidRequest):
            service.complete(ADMIN, started["id"])
        self.assertNotIn(("backend-a", True), gw.calls)


class AbortTest(unittest.TestCase):
    def test_abort_restores_traffic(self):
        service, gw, _ = build(running=3)
        started = service.start(ADMIN, "prod-a", "restart")
        result = service.abort(ADMIN, started["id"], "changed my mind")
        self.assertEqual(ABORTED, result["state"])
        self.assertEqual(("backend-a", True), gw.calls[-1])

    def test_a_failed_abort_stays_visible(self):
        """The cluster is still receiving nothing; it must not look finished."""
        gw = FakeGateway()
        service, _, _ = build(running=1, gateway=gw)
        started = service.start(ADMIN, "prod-a", "restart")
        gw.fail = True
        with self.assertRaises(Exception):
            service.abort(ADMIN, started["id"])
        active = service.active()
        self.assertEqual(1, len(active), "still holding traffic, still listed")
        self.assertIn("still receiving no queries", active[0]["history"][-1]["message"])


class LiveViewTest(unittest.TestCase):
    def test_refresh_adds_progress_lines_while_draining(self):
        service, _, snapshots = build(running=5)
        started = service.start(ADMIN, "prod-a", "restart")
        snapshots.save(Snapshot("prod-a", KIND_QUERIES, utcnow(),
                                payload={"summary": {"running": 2, "queued": 0}}))
        state = service.refresh(ADMIN, started["id"])
        messages = [h["message"] for h in state["history"]]
        self.assertTrue(any("Waiting for 2 running queries" in m for m in messages))

    def test_the_view_carries_the_step_checklist(self):
        service, _, _ = build(running=0)
        started = service.start(ADMIN, "prod-a", "restart")
        statuses = {s["state"]: s["status"] for s in started["steps"]}
        self.assertEqual("done", statuses[DRAINING])
        self.assertEqual("current", statuses[DRAINED])
        self.assertEqual("pending", statuses[RESTARTING])


if __name__ == "__main__":
    unittest.main()
