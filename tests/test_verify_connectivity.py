"""Tests for scripts/verify_connectivity.py (Bolt 2 / V1).

The script runs against production coordinators, so its parsing and its
failure-detection logic are tested here with injected responses instead of a
live cluster.

The most important case is CASE B: with `file` access control, a denied
`queries` rule surfaces as an EMPTY LIST rather than a 403. That is
indistinguishable from an idle cluster, so the script cross-checks the query
list against the JMX RunningQueries counter (health test H-09). If that
cross-check ever stops firing, TMS can silently report "0 running queries" on a
busy cluster.

Standard library only - no Artifactory round trip required to run these.
"""

import importlib.util
import json
import os
import unittest
import urllib.parse

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "verify_connectivity.py",
)

_spec = importlib.util.spec_from_file_location("verify_connectivity", _SCRIPT)
vc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vc)

BASE = "http://coordinator.invalid"

INFO_BODY = {
    "nodeVersion": {"version": "477"},
    "environment": "prod",
    "coordinator": True,
    "starting": False,
    "uptime": "1.00d",
}

SAMPLE_QUERY = {
    "queryId": "20260806_041200_00042_abcde",
    "state": "RUNNING",
    "query": "SELECT 1",
    "resourceGroupId": ["global", "bi"],
    "queryStats": {
        "elapsedTime": "1.00s",
        "totalCpuTime": "2.00s",
        "progressPercentage": 50.0,
    },
}


def fake_request(queries, running_queries, active_count=12, jmx_status=200):
    """Build a stand-in for vc.request with controllable responses."""

    def _request(opener, url, user, password):
        path = url[len(BASE) :]
        if path.startswith("/v1/info/state"):
            return 200, '"ACTIVE"', 0.01
        if path.startswith("/v1/info"):
            return 200, json.dumps(INFO_BODY), 0.01
        if path.startswith("/v1/jmx/mbean"):
            if jmx_status != 200:
                return jmx_status, "Management only resource", 0.01
            if path == "/v1/jmx/mbean":
                return 200, json.dumps([{"objectName": "x"}]), 0.01
            name = urllib.parse.unquote(path[len("/v1/jmx/mbean/") :])
            if "HeartbeatFailureDetector" in name:
                attrs = [{"name": "ActiveCount", "value": active_count}]
            elif "QueryManager" in name:
                attrs = [{"name": "RunningQueries", "value": running_queries}]
            else:
                attrs = [{"name": "Whatever", "value": 1}]
            return 200, json.dumps({"attributes": attrs}), 0.01
        if path.startswith("/v1/query"):
            return 200, json.dumps(queries), 0.01
        if path.startswith("/metrics"):
            return 200, "# HELP x\ntrino_a 1\njvm_b{k=\"v\"} 2\n", 0.01
        return 404, "{}", 0.01

    return _request


class VerifyConnectivityTest(unittest.TestCase):
    def setUp(self):
        self._original_request = vc.request

    def tearDown(self):
        vc.request = self._original_request

    def test_healthy_environment_produces_no_failures(self):
        vc.request = fake_request([SAMPLE_QUERY], running_queries=3)
        result = vc.Result()
        vc.check_v1_1_info(None, BASE, result)
        vc.check_v1_2_3_jmx(None, BASE, "tms-svc", "pw", 12, result)
        vc.check_v1_4_5_queries(None, BASE, "tms-svc", "pw", result)
        vc.check_v1_7_metrics(None, BASE, "tms-svc", "pw", result)
        self.assertEqual(result.failed, [], "unexpected failures: {}".format(result.failed))

    def test_silent_query_filtering_is_detected(self):
        """Empty list + RunningQueries > 0 must be reported, not accepted."""
        vc.request = fake_request([], running_queries=7)
        result = vc.Result()
        vc.check_v1_4_5_queries(None, BASE, "tms-svc", "pw", result)
        self.assertTrue(
            any("H-09" in check for check in result.failed),
            "H-09 cross-check did not fire; silent filtering would go unnoticed",
        )

    def test_genuinely_idle_cluster_is_not_flagged(self):
        """Empty list AND RunningQueries == 0 is a normal idle cluster."""
        vc.request = fake_request([], running_queries=0)
        result = vc.Result()
        vc.check_v1_4_5_queries(None, BASE, "tms-svc", "pw", result)
        self.assertEqual(result.failed, [], "idle cluster was misreported as a failure")

    def test_jmx_forbidden_is_actionable(self):
        vc.request = fake_request([SAMPLE_QUERY], running_queries=3, jmx_status=403)
        result = vc.Result()
        vc.check_v1_2_3_jmx(None, BASE, "tms-svc", "pw", 12, result)
        self.assertIn("V1-2", result.failed)

    def test_active_count_records_observed_value(self):
        """H-03 thresholds depend on whether the coordinator is counted."""
        for active, expected_workers in ((12, 12), (13, 12)):
            with self.subTest(active=active):
                vc.request = fake_request(
                    [SAMPLE_QUERY], running_queries=3, active_count=active
                )
                result = vc.Result()
                vc.check_v1_2_3_jmx(None, BASE, "tms-svc", "pw", expected_workers, result)
                self.assertEqual(result.facts.get("active_count"), active)

    def test_live_states_exclude_terminal_states(self):
        """Completed queries belong to the separate history project (D-001)."""
        self.assertNotIn("FINISHED", vc.LIVE_STATES)
        self.assertNotIn("FAILED", vc.LIVE_STATES)

    def test_health_mbean_names_are_documented_ones(self):
        """Every MBean must appear verbatim in the Trino 477 docs."""
        for name in vc.HEALTH_MBEANS:
            self.assertTrue(
                name.startswith("trino.") or name.startswith("java.lang:"),
                "unexpected MBean namespace: {}".format(name),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
