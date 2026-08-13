"""The restart gate that asks whether a cluster could start again (D-010).

Trino 477's db resource group manager exits at startup if it cannot read its
store, so the safe sequence must refuse to stop a cluster it could not bring
back. These tests cover the three layers separately: what the probe concludes,
what the state machine does with that conclusion, and where the service asks.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.api.errors import InvalidRequest  # noqa: E402
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
from tms.core.config import ConfigError, build_config  # noqa: E402
from tms.ops.config_store import (  # noqa: E402
    ResourceGroupStore,
    StoreProbe,
    valid_schema_name,
)
from tms.ops.executor import ManualExecutor  # noqa: E402
from tms.ops.repository import InMemorySequenceRepository  # noqa: E402
from tms.ops.sequence import (  # noqa: E402
    DRAINED,
    DRAINING,
    RESTARTING,
    RestartSequence,
    StepBlocked,
)
from tms.ops.service import RestartService  # noqa: E402

ADMIN = Principal("op", ["admin"], ip="10.0.0.9")


# --------------------------------------------------------------------- fakes


class FakeCursor:
    def __init__(self, row, error=None):
        self._row = row
        self._error = error
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self._error is not None:
            raise self._error
        self.executed.append((sql, params))

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor


class FakeStore(ResourceGroupStore):
    """A ResourceGroupStore whose only fake part is the socket."""

    def __init__(self, row=None, error=None, connect_error=None,
                 schema="trino_resource_groups"):
        super().__init__("postgresql://u:p@h/d", schema)
        self.cursor = FakeCursor(row, error)
        self._connect_error = connect_error

    def _connect(self):
        if self._connect_error is not None:
            raise self._connect_error
        return FakeConnection(self.cursor)


class FakeProbeStore:
    def __init__(self, probe):
        self.probe_result = probe
        self.calls = []

    def probe(self, environment):
        self.calls.append(environment)
        return self.probe_result


class FakeGateway:
    def __init__(self):
        self.calls = []

    def set_active(self, name, active):
        self.calls.append((name, active))


def build(config_store=None, node_environment="cluster1", running=0):
    config = build_config({
        "clusters": [{"name": "prod-a", "coordinator_url": "https://a.invalid:8443",
                      "expected_workers": 11,
                      "node_environment": node_environment}],
        "trino": {"user": "u", "password": "p"},
        "database": {"url": "postgresql://u:p@h/d"},
        "resource_groups": {"enabled": True},
    })
    snapshots = InMemorySnapshotRepository()
    snapshots.save(Snapshot("prod-a", KIND_QUERIES, utcnow(),
                            payload={"summary": {"running": running, "queued": 0}}))
    snapshots.save(Snapshot("prod-a", KIND_HEALTH, utcnow(),
                            payload={"rollup_state": "GOOD"}))
    snapshots.save(Snapshot(GATEWAY_SCOPE, KIND_GATEWAY, utcnow(), payload={
        "backends": [{"name": "backend-a", "cluster": "prod-a"}]}))
    gateway = FakeGateway()
    service = RestartService(
        config=config, repository=InMemorySequenceRepository(), snapshots=snapshots,
        gateway_client=gateway, audit_guard=AuditGuard(InMemoryAuditRepository()),
        executor=ManualExecutor(), config_store=config_store)
    return service, gateway


# ---------------------------------------------------------------- the probe


class ProbeTest(unittest.TestCase):
    def test_groups_and_selectors_present_is_ready(self):
        probe = FakeStore(row=(3, 2)).probe("cluster1")
        self.assertIs(True, probe.ready)
        self.assertIn("cluster1", probe.detail)

    def test_no_rows_for_this_environment_blocks(self):
        """The failure this exists to catch: cluster1 loaded, cluster2 forgotten."""
        probe = FakeStore(row=(0, 0)).probe("cluster2")
        self.assertIs(False, probe.ready)
        self.assertIn("cluster2", probe.detail)
        self.assertIn("no rows", probe.detail.lower())

    def test_groups_without_selectors_blocks(self):
        """A tree nothing routes into is not a working configuration."""
        probe = FakeStore(row=(3, 0)).probe("cluster1")
        self.assertIs(False, probe.ready)
        self.assertIn("no selectors", probe.detail.lower())

    def test_unreachable_database_blocks_and_says_so(self):
        probe = FakeStore(connect_error=OSError("connection refused")).probe("cluster1")
        self.assertIs(False, probe.ready)
        self.assertIn("unreachable", probe.detail.lower())
        self.assertIn("connection refused", probe.detail)

    def test_missing_tables_are_reported_differently_from_an_outage(self):
        """Different next action: a setup step was skipped, not a database down."""
        probe = FakeStore(
            error=RuntimeError('relation "x.resource_groups" does not exist'),
        ).probe("cluster1")
        self.assertIs(False, probe.ready)
        self.assertIn("not found", probe.detail.lower())

    def test_a_cluster_without_node_environment_gets_no_opinion(self):
        """Abstaining beats blocking every restart over an optional setting."""
        probe = FakeStore(row=(3, 2)).probe("")
        self.assertIsNone(probe.ready)

    def test_probe_never_raises(self):
        probe = FakeStore(connect_error=ValueError("anything at all")).probe("cluster1")
        self.assertIs(False, probe.ready)

    def test_schema_name_must_be_an_identifier(self):
        self.assertTrue(valid_schema_name("trino_resource_groups"))
        for bad in ('public"; drop table x --', "a b", "", "1abc", "sch;ema"):
            self.assertFalse(valid_schema_name(bad), bad)
        with self.assertRaises(ValueError):
            ResourceGroupStore("postgresql://u:p@h/d", "bad name")


# ------------------------------------------------------------ the sequence


def drained_sequence():
    sequence = RestartSequence("prod-a", "config change", "op")
    sequence.begin()
    sequence.observe(running_queries=0)
    sequence.confirm_drained()
    return sequence


class SequenceGateTest(unittest.TestCase):
    def test_restart_is_blocked_when_the_store_cannot_serve_the_cluster(self):
        sequence = drained_sequence()
        sequence.observe_config_store(False, "no rows for 'cluster1'")
        with self.assertRaises(StepBlocked) as caught:
            sequence.mark_restarting()
        self.assertIn("would not start again", str(caught.exception))
        self.assertEqual(DRAINED, sequence.state, "the cluster was not stopped")

    def test_restart_proceeds_when_the_store_is_ready(self):
        sequence = drained_sequence()
        sequence.observe_config_store(True, "3 groups, 2 selectors")
        sequence.mark_restarting()
        self.assertEqual(RESTARTING, sequence.state)

    def test_no_opinion_does_not_block(self):
        """File-manager installs must keep working exactly as before."""
        sequence = drained_sequence()
        sequence.observe_config_store(None, "")
        sequence.mark_restarting()
        self.assertEqual(RESTARTING, sequence.state)

    def test_a_blocked_store_is_logged_once_not_per_poll(self):
        sequence = drained_sequence()
        for _ in range(5):
            sequence.observe_config_store(False, "database is unreachable")
        warnings = [line for line in sequence.history
                    if line["level"] == "warn" and "unreachable" in line["message"]]
        self.assertEqual(1, len(warnings), sequence.history)

    def test_recovery_is_logged_too(self):
        sequence = drained_sequence()
        sequence.observe_config_store(False, "database is unreachable")
        sequence.observe_config_store(True, "3 groups, 2 selectors")
        sequence.mark_restarting()
        self.assertIn("3 groups, 2 selectors",
                      [line["message"] for line in sequence.history])


# ------------------------------------------------------------- the service


class ServiceTest(unittest.TestCase):
    def test_restart_is_refused_while_the_store_is_down(self):
        store = FakeProbeStore(StoreProbe(False, "the store is unreachable"))
        service, gateway = build(config_store=store)
        started = service.start(ADMIN, "prod-a", "config change")

        with self.assertRaises(InvalidRequest) as caught:
            service.restart(ADMIN, started["id"])
        self.assertIn("would not start again", str(caught.exception))

        payload = service.get(ADMIN, started["id"])
        self.assertEqual(DRAINED, payload["state"])
        self.assertEqual([("backend-a", False)], gateway.calls,
                         "traffic stayed blocked; nothing was restarted")

    def test_restart_proceeds_when_the_store_is_ready(self):
        store = FakeProbeStore(StoreProbe(True, "3 groups, 2 selectors"))
        service, _ = build(config_store=store)
        started = service.start(ADMIN, "prod-a", "config change")
        payload = service.restart(ADMIN, started["id"])
        self.assertEqual(RESTARTING, payload["state"])

    def test_the_store_is_checked_during_the_drain_not_only_at_the_button(self):
        """So a stuck store is visible while there is still time to fix it."""
        store = FakeProbeStore(StoreProbe(False, "the store is unreachable"))
        service, _ = build(config_store=store, running=3)
        started = service.start(ADMIN, "prod-a", "config change")
        payload = service.refresh(ADMIN, started["id"])
        self.assertEqual(DRAINING, payload["state"])
        self.assertIs(False, payload["config_store_ready"])
        self.assertEqual(["cluster1"], store.calls[:1])

    def test_the_probe_uses_this_cluster_node_environment(self):
        store = FakeProbeStore(StoreProbe(True, "ok"))
        service, _ = build(config_store=store, node_environment="cluster2")
        started = service.start(ADMIN, "prod-a", "config change")
        service.restart(ADMIN, started["id"])
        self.assertEqual({"cluster2"}, set(store.calls))

    def test_without_a_store_the_sequence_behaves_exactly_as_before(self):
        service, _ = build(config_store=None)
        started = service.start(ADMIN, "prod-a", "config change")
        payload = service.restart(ADMIN, started["id"])
        self.assertEqual(RESTARTING, payload["state"])
        self.assertIsNone(payload["config_store_ready"])


class ConfigTest(unittest.TestCase):
    def test_schema_is_rejected_at_load_time_when_it_is_not_an_identifier(self):
        with self.assertRaises(ConfigError):
            build_config({
                "clusters": [{"name": "a", "coordinator_url": "https://a.invalid",
                              "expected_workers": 1}],
                "trino": {"user": "u", "password": "p"},
                "database": {"url": "postgresql://u:p@h/d"},
                "resource_groups": {"enabled": True, "schema": "not an identifier"},
            })

    def test_a_bad_schema_is_ignored_while_the_feature_is_off(self):
        """Off means off - an unused setting must not stop tms-api booting."""
        config = build_config({
            "clusters": [{"name": "a", "coordinator_url": "https://a.invalid",
                          "expected_workers": 1}],
            "trino": {"user": "u", "password": "p"},
            "database": {"url": "postgresql://u:p@h/d"},
            "resource_groups": {"schema": "not an identifier"},
        })
        self.assertFalse(config.resource_groups.enabled)

    def test_node_environment_is_read_from_the_cluster_entry(self):
        config = build_config({
            "clusters": [{"name": "a", "coordinator_url": "https://a.invalid",
                          "expected_workers": 1, "node_environment": " cluster1 "}],
            "trino": {"user": "u", "password": "p"},
            "database": {"url": "postgresql://u:p@h/d"},
        })
        self.assertEqual("cluster1", config.cluster("a").node_environment)


if __name__ == "__main__":
    unittest.main()
