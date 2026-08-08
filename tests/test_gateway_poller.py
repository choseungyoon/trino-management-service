"""Tests for Gateway polling and the join with what TMS monitors.

The join is the whole point of the screen. Gateway's own UI shows its backends
and TMS shows its clusters, and both look right; only putting them side by side
reveals that they disagree — a backend nobody monitors, or a monitored cluster
that receives no traffic. Neither is visible from either side alone.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.clients.errors import TrinoForbidden  # noqa: E402
from tms.collector.gateway_poller import GatewayPoller, join_backends  # noqa: E402
from tms.collector.snapshot import (  # noqa: E402
    GATEWAY_SCOPE,
    KIND_GATEWAY,
    InMemorySnapshotRepository,
)
from tms.core.config import ClusterConfig  # noqa: E402


def cluster(name, url):
    return ClusterConfig(name=name, coordinator_url=url, expected_workers=12)


def backend(name, proxy_to, active=True, group="adhoc"):
    return {"name": name, "proxyTo": proxy_to, "active": active,
            "routingGroup": group, "externalUrl": proxy_to}


class FakeGateway:
    def __init__(self, backends=None, rules=None, live=True, raise_on_list=None):
        self._backends = backends or []
        self._rules = rules
        self._live = live
        self._raise = raise_on_list

    def list_backends(self):
        if self._raise:
            raise self._raise
        return self._backends

    def get_routing_rules(self):
        return self._rules

    def is_live(self):
        return self._live


class JoinTest(unittest.TestCase):
    def test_matches_on_url_even_when_names_differ(self):
        """Measured live: the Gateway backend was 'local-trino-477' while the
        TMS cluster was 'local-a'. Names are chosen independently on each side;
        the URL is what actually decides where a query lands."""
        result = join_backends(
            [backend("local-trino-477", "https://a.invalid:8443")],
            [cluster("prod-a", "https://a.invalid:8443")])
        row = result["backends"][0]
        self.assertEqual("prod-a", row["cluster"])
        self.assertEqual("url", row["matched_by"])

    def test_trailing_slash_does_not_break_the_match(self):
        result = join_backends(
            [backend("b", "https://a.invalid:8443/")],
            [cluster("prod-a", "https://a.invalid:8443")])
        self.assertEqual("prod-a", result["backends"][0]["cluster"])

    def test_falls_back_to_name_and_says_so(self):
        """A name match is weaker - it should be visible that it was used."""
        result = join_backends(
            [backend("prod-a", "https://elsewhere.invalid:8443")],
            [cluster("prod-a", "https://a.invalid:8443")])
        row = result["backends"][0]
        self.assertEqual("prod-a", row["cluster"])
        self.assertEqual("name", row["matched_by"])

    def test_backend_nobody_monitors_is_surfaced(self):
        """Queries are routed there and no one is watching."""
        result = join_backends(
            [backend("rogue", "https://rogue.invalid:8443")],
            [cluster("prod-a", "https://a.invalid:8443")])
        self.assertEqual(["rogue"], result["unmonitored_backends"])
        self.assertIsNone(result["backends"][0]["cluster"])

    def test_cluster_with_no_backend_is_surfaced(self):
        """TMS is watching something that receives no traffic."""
        result = join_backends(
            [backend("prod-a", "https://a.invalid:8443")],
            [cluster("prod-a", "https://a.invalid:8443"),
             cluster("prod-b", "https://b.invalid:8443")])
        self.assertEqual(["prod-b"], result["unrouted_clusters"])

    def test_inactive_backends_are_listed(self):
        result = join_backends(
            [backend("prod-a", "https://a.invalid:8443", active=False)],
            [cluster("prod-a", "https://a.invalid:8443")])
        self.assertEqual(["prod-a"], result["inactive_backends"])

    def test_groups_are_aggregated(self):
        result = join_backends([
            backend("a", "https://a.invalid:8443", group="adhoc"),
            backend("b", "https://b.invalid:8443", group="adhoc", active=False),
            backend("c", "https://c.invalid:8443", group="etl"),
        ], [])
        groups = {g["name"]: g for g in result["groups"]}
        self.assertEqual(1, groups["adhoc"]["active"])
        self.assertEqual(2, groups["adhoc"]["total"])
        self.assertEqual(1, groups["etl"]["total"])

    def test_backend_with_no_group_is_labelled(self):
        result = join_backends([backend("a", "https://a.invalid:8443", group="")], [])
        self.assertEqual("(none)", result["groups"][0]["name"])

    def test_no_clusters_configured_does_not_crash(self):
        result = join_backends([backend("a", "https://a.invalid:8443")], [])
        self.assertEqual(["a"], result["unmonitored_backends"])
        self.assertEqual([], result["unrouted_clusters"])


class PollTest(unittest.TestCase):
    def test_snapshot_is_fleet_scoped(self):
        """One Gateway deployment behind a load balancer - not per-cluster."""
        poller = GatewayPoller(FakeGateway([backend("a", "https://a.invalid:8443")]),
                               InMemorySnapshotRepository(), clusters=[])
        snapshot = poller.poll()
        self.assertEqual(GATEWAY_SCOPE, snapshot.cluster)
        self.assertEqual(KIND_GATEWAY, snapshot.kind)

    def test_failure_is_recorded_with_advice_not_raised(self):
        poller = GatewayPoller(FakeGateway(raise_on_list=TrinoForbidden("403")),
                               InMemorySnapshotRepository(), clusters=[])
        snapshot = poller.poll()
        self.assertFalse(snapshot.trustworthy)
        self.assertIn("TrinoForbidden", snapshot.collection_error)
        self.assertIn("rules.json", snapshot.advice)

    def test_absent_routing_rules_are_not_an_error(self):
        """The endpoint is undocumented and 500s when unconfigured."""
        poller = GatewayPoller(FakeGateway([backend("a", "https://a.invalid:8443")],
                                           rules=None),
                               InMemorySnapshotRepository(), clusters=[])
        snapshot = poller.poll()
        self.assertIsNone(snapshot.collection_error)
        self.assertIsNone(snapshot.payload["routing_rules"])

    def test_tick_persists_and_reschedules(self):
        repository = InMemorySnapshotRepository()
        clock = [1000.0]
        poller = GatewayPoller(FakeGateway([backend("a", "https://a.invalid:8443")]),
                               repository, clusters=[], interval=30.0,
                               clock=lambda: clock[0])
        self.assertEqual(1, len(poller.tick()))
        self.assertIsNotNone(repository.load(GATEWAY_SCOPE, KIND_GATEWAY))
        self.assertEqual([], poller.tick(), "not due yet")
        clock[0] += 31
        self.assertEqual(1, len(poller.tick()))

    def test_a_failed_poll_still_reschedules(self):
        """Otherwise a broken Gateway is retried in a tight loop."""
        clock = [1000.0]
        poller = GatewayPoller(FakeGateway(raise_on_list=TrinoForbidden("403")),
                               InMemorySnapshotRepository(), clusters=[],
                               interval=30.0, clock=lambda: clock[0])
        poller.tick()
        self.assertEqual([], poller.tick())


if __name__ == "__main__":
    unittest.main()
