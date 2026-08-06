"""Tests for TrinoClient using a fake transport.

Properties worth protecting, in rough order of how badly they would hurt:

* No SQL, ever. TMS polling `system.runtime.*` would consume coordinator query
  slots and pollute the separate query-history project's data (principle A1).
* No `X-Trino-User`. Sending it turns every call into an impersonation check.
* Kills are never retried - a retry can kill twice.
* 403 stays a 403. It must not be laundered into "unavailable" by the retry or
  circuit-breaker paths, because only the 403 message names the fix.
* /v1/info carries no credentials: it is the last signal that still works when
  authorisation is broken.
"""

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.clients.circuit import CircuitBreaker  # noqa: E402
from tms.clients.errors import (  # noqa: E402
    CircuitOpen,
    MBeanNotRegistered,
    TrinoForbidden,
    TrinoNotFound,
    TrinoProtocolError,
    TrinoUnauthorized,
    TrinoUnavailable,
)
from tms.clients.transport import HttpResponse, Transport, TransportError  # noqa: E402
from tms.clients.trino import (  # noqa: E402
    LIVE_STATES,
    NODE_MANAGER_MBEAN,
    TrinoClient,
    build_kill_message,
)


class FakeTransport(Transport):
    """Records every call and replays scripted responses."""

    def __init__(self, responses=None, raise_transport_error=False):
        self.calls = []
        self.responses = responses or {}
        self.raise_transport_error = raise_transport_error
        self.default = HttpResponse(200, b"{}")

    def request(
        self,
        method,
        url,
        headers=None,
        body=None,
        connect_timeout=2.0,
        read_timeout=5.0,
        verify_tls=True,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "connect_timeout": connect_timeout,
                "read_timeout": read_timeout,
            }
        )
        if self.raise_transport_error:
            raise TransportError("connection refused")
        for fragment, response in self.responses.items():
            if fragment in url:
                if isinstance(response, list):
                    return response.pop(0) if len(response) > 1 else response[0]
                return response
        return self.default


def json_response(payload, status=200):
    return HttpResponse(status, json.dumps(payload).encode("utf-8"))


def make_client(transport, **kwargs):
    kwargs.setdefault("read_retries", 2)
    kwargs.setdefault("sleep", lambda _seconds: None)
    return TrinoClient(
        base_url="https://coordinator.invalid:8443",
        user="tms-svc",
        password="pw",
        transport=transport,
        **kwargs
    )


class NoSqlAndNoImpersonationTest(unittest.TestCase):
    def test_no_call_ever_submits_sql(self):
        """Principle A1: REST and JMX only, never /v1/statement."""
        transport = FakeTransport(
            {
                "/v1/info": json_response({"starting": False}),
                "/v1/query": json_response([]),
                "/v1/jmx/mbean/": json_response({"attributes": []}),
            }
        )
        client = make_client(transport)
        client.get_server_info()
        client.list_queries()
        client.get_mbean("java.lang:type=Memory")
        for call in transport.calls:
            self.assertNotIn("/v1/statement", call["url"])
            self.assertNotIn("system.runtime", call["url"])
            self.assertNotIn("SELECT", (call["body"] or b"").decode("utf-8", "replace").upper())

    def test_authenticated_calls_never_send_x_trino_user(self):
        transport = FakeTransport({"/v1/query": json_response([])})
        make_client(transport).list_queries()
        for call in transport.calls:
            lowered = {k.lower() for k in call["headers"]}
            self.assertNotIn("x-trino-user", lowered)

    def test_public_endpoints_send_no_credentials(self):
        """/v1/info must keep working when authorisation is broken."""
        transport = FakeTransport({"/v1/info": json_response({"starting": False})})
        make_client(transport).get_server_info()
        self.assertEqual(transport.calls[0]["headers"], {})

    def test_authenticated_calls_send_basic_auth(self):
        transport = FakeTransport({"/v1/query": json_response([])})
        make_client(transport).list_queries()
        self.assertTrue(transport.calls[0]["headers"]["Authorization"].startswith("Basic "))


class QueryListingTest(unittest.TestCase):
    def test_requests_only_live_states(self):
        transport = FakeTransport({"/v1/query": json_response([])})
        make_client(transport).list_queries()
        url = transport.calls[0]["url"]
        for state in LIVE_STATES:
            self.assertIn("state=" + state, url)
        # Completed queries belong to the separate history project (D-001).
        self.assertNotIn("state=FINISHED", url)
        self.assertNotIn("state=FAILED", url)

    def test_reports_response_size_for_backoff(self):
        payload = [{"queryId": "q{}".format(i)} for i in range(3)]
        transport = FakeTransport({"/v1/query": json_response(payload)})
        result = make_client(transport).list_queries()
        self.assertEqual(len(result), 3)
        self.assertGreater(result.response_bytes, 0)

    def test_non_dict_entries_are_dropped(self):
        transport = FakeTransport({"/v1/query": json_response([{"queryId": "a"}, "junk", None])})
        self.assertEqual(len(make_client(transport).list_queries()), 1)

    def test_invalid_json_raises_protocol_error(self):
        transport = FakeTransport({"/v1/query": HttpResponse(200, b"not json")})
        with self.assertRaises(TrinoProtocolError):
            make_client(transport).list_queries()


class NodeCountsTest(unittest.TestCase):
    def _mbean(self, **counts):
        attributes = [{"name": k, "value": v} for k, v in counts.items()]
        return json_response({"attributes": attributes})

    def test_reads_all_node_counts(self):
        transport = FakeTransport(
            {
                "/v1/jmx/mbean/": self._mbean(
                    ActiveNodeCount=13,
                    InactiveNodeCount=0,
                    DrainingNodeCount=1,
                    DrainedNodeCount=0,
                    ShuttingDownNodeCount=0,
                )
            }
        )
        counts = make_client(transport).get_node_counts()
        self.assertEqual(counts["ActiveNodeCount"], 13)
        self.assertEqual(counts["DrainingNodeCount"], 1)

    def test_uses_the_verified_mbean_not_the_documented_one(self):
        transport = FakeTransport({"/v1/jmx/mbean/": self._mbean(ActiveNodeCount=13)})
        make_client(transport).get_node_counts()
        url = transport.calls[0]["url"]
        self.assertIn("CoordinatorNodeManager", url)
        self.assertNotIn("failuredetector", url.lower())

    def test_missing_active_node_count_is_a_protocol_error(self):
        transport = FakeTransport({"/v1/jmx/mbean/": self._mbean(InactiveNodeCount=0)})
        with self.assertRaises(TrinoProtocolError):
            make_client(transport).get_node_counts()

    def test_mbean_500_is_reported_as_not_registered(self):
        """A stale MBean name answers 500, not 404 - airlift maps no JMException."""
        transport = FakeTransport({"/v1/jmx/mbean/": HttpResponse(500, b"boom")})
        with self.assertRaises(MBeanNotRegistered) as ctx:
            make_client(transport).get_mbean(NODE_MANAGER_MBEAN)
        self.assertIn("/v1/jmx/mbean", str(ctx.exception))
        self.assertTrue(ctx.exception.advice)


class ErrorClassificationTest(unittest.TestCase):
    def test_403_is_not_transient_and_is_not_retried(self):
        transport = FakeTransport({"/v1/query": HttpResponse(403, b"denied")})
        client = make_client(transport)
        with self.assertRaises(TrinoForbidden) as ctx:
            client.list_queries()
        self.assertEqual(len(transport.calls), 1, "403 must not be retried")
        self.assertFalse(ctx.exception.transient)
        self.assertIn("rules.json", ctx.exception.advice)

    def test_403_does_not_open_the_circuit(self):
        """Otherwise the actionable message is replaced by a generic outage."""
        transport = FakeTransport({"/v1/query": HttpResponse(403, b"denied")})
        breaker = CircuitBreaker(failure_threshold=2)
        client = make_client(transport, breaker=breaker)
        for _ in range(5):
            with self.assertRaises(TrinoForbidden):
                client.list_queries()
        self.assertTrue(breaker.allows_request())

    def test_401_is_reported_distinctly(self):
        transport = FakeTransport({"/v1/query": HttpResponse(401, b"")})
        with self.assertRaises(TrinoUnauthorized):
            make_client(transport).list_queries()

    def test_404_is_reported_distinctly(self):
        transport = FakeTransport({"/v1/query/": HttpResponse(404, b"")})
        with self.assertRaises(TrinoNotFound):
            make_client(transport).get_query("gone")

    def test_5xx_is_transient_and_retried(self):
        transport = FakeTransport({"/v1/query": HttpResponse(503, b"")})
        with self.assertRaises(TrinoUnavailable):
            make_client(transport, read_retries=2).list_queries()
        self.assertEqual(len(transport.calls), 3, "reads should retry twice")

    def test_transport_error_is_transient_and_retried(self):
        transport = FakeTransport(raise_transport_error=True)
        with self.assertRaises(TrinoUnavailable):
            make_client(transport, read_retries=2).list_queries()
        self.assertEqual(len(transport.calls), 3)

    def test_every_error_carries_advice(self):
        for error in (
            TrinoUnavailable("x"),
            TrinoForbidden("x"),
            TrinoUnauthorized("x"),
            TrinoNotFound("x"),
            MBeanNotRegistered("x"),
            CircuitOpen("x"),
        ):
            self.assertTrue(error.advice, "{} has no advice".format(type(error).__name__))


class CircuitIntegrationTest(unittest.TestCase):
    def test_circuit_opens_after_repeated_outages_and_short_circuits(self):
        transport = FakeTransport(raise_transport_error=True)
        breaker = CircuitBreaker(failure_threshold=2)
        client = make_client(transport, breaker=breaker, read_retries=0)

        for _ in range(2):
            with self.assertRaises(TrinoUnavailable):
                client.list_queries()

        calls_before = len(transport.calls)
        with self.assertRaises(CircuitOpen):
            client.list_queries()
        self.assertEqual(len(transport.calls), calls_before, "open circuit must not call out")


class KillQueryTest(unittest.TestCase):
    def test_kill_is_never_retried(self):
        transport = FakeTransport({"/killed": HttpResponse(503, b"")})
        client = make_client(transport, read_retries=3)
        with self.assertRaises(TrinoUnavailable):
            client.kill_query("q1", "because")
        self.assertEqual(len(transport.calls), 1, "a retried kill can kill twice")

    def test_kill_uses_put_killed_and_sends_reason_as_body(self):
        transport = FakeTransport({"/killed": HttpResponse(200, b"")})
        client = make_client(transport)
        message = build_kill_message("syhcho", "resource hog", "req-1")
        client.kill_query("20260806_1_abc", message)
        call = transport.calls[0]
        self.assertEqual(call["method"], "PUT")
        self.assertTrue(call["url"].endswith("/v1/query/20260806_1_abc/killed"))
        self.assertIn(b"resource hog", call["body"])

    def test_kill_uses_the_write_timeout(self):
        transport = FakeTransport({"/killed": HttpResponse(200, b"")})
        make_client(transport, write_timeout=10.0).kill_query("q", "m")
        self.assertEqual(transport.calls[0]["read_timeout"], 10.0)

    def test_kill_message_is_single_line_and_capped(self):
        message = build_kill_message("a", "line1\nline2\t  spaced" + "x" * 800, "r")
        self.assertNotIn("\n", message)
        self.assertNotIn("\t", message)
        self.assertLess(len(message), 700)

    def test_kill_message_carries_actor_and_request_id(self):
        message = build_kill_message("syhcho", "why", "req-42")
        self.assertIn("actor=syhcho", message)
        self.assertIn("request_id=req-42", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
