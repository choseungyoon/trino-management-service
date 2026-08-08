"""Tests for the Trino Gateway client.

Every case here corresponds to something measured against a real Gateway 19
(TRINO_VERIFIED.md T2-3-1). The ones that matter are the traps:

* delete answers 200 to bodies it ignores, so a 200 proves nothing
* the routing rules endpoint is undocumented and 500s when unconfigured
* readyz is 200 with no backends, so it is not a routing-readiness signal
"""

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.clients.errors import TrinoForbidden, TrinoProtocolError  # noqa: E402
from tms.clients.gateway import (  # noqa: E402
    BACKEND_DELETE,
    GatewayClient,
    GatewayWriteNotApplied,
)
from tms.clients.transport import HttpResponse, TransportError  # noqa: E402

BACKEND = {"name": "prod-a", "proxyTo": "https://a.invalid:8443", "active": True,
           "routingGroup": "adhoc", "externalUrl": "https://a.invalid:8443"}


class FakeTransport:
    """Replays canned responses and records what was sent."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def request(self, method, url, headers=None, body=None, **kwargs):
        path = url.split("8090", 1)[-1] if "8090" in url else url
        self.calls.append({"method": method, "path": path, "body": body,
                           "headers": headers or {}})
        handler = self.responses.get(path)
        if handler is None:
            return HttpResponse(404, b"", 0.0)
        if isinstance(handler, Exception):
            raise handler
        if callable(handler):
            return handler(self)
        return handler


def ok(payload):
    return HttpResponse(200, json.dumps(payload).encode("utf-8"), 0.01)


def client(transport):
    return GatewayClient(base_url="http://gw:8090", transport=transport,
                         sleep=lambda _s: None)


class ReadTest(unittest.TestCase):
    def test_list_backends(self):
        gw = client(FakeTransport({"/gateway/backend/all": ok([BACKEND])}))
        self.assertEqual(["prod-a"], [b["name"] for b in gw.list_backends()])

    def test_active_only_uses_the_other_path(self):
        transport = FakeTransport({"/gateway/backend/active": ok([BACKEND])})
        client(transport).list_backends(active_only=True)
        self.assertEqual("/gateway/backend/active", transport.calls[0]["path"])

    def test_non_list_response_is_a_protocol_error(self):
        gw = client(FakeTransport({"/gateway/backend/all": ok({"oops": 1})}))
        with self.assertRaises(TrinoProtocolError):
            gw.list_backends()

    def test_forbidden_propagates(self):
        gw = client(FakeTransport({"/gateway/backend/all": HttpResponse(403, b"", 0.0)}))
        with self.assertRaises(TrinoForbidden):
            gw.list_backends()


class RoutingRulesTest(unittest.TestCase):
    """Undocumented endpoint - absence must degrade, not fail."""

    def test_rules_are_unwrapped_from_the_envelope(self):
        payload = {"code": 200, "msg": "Successful.",
                   "data": [{"name": "adhoc-header", "priority": 0}]}
        gw = client(FakeTransport({"/webapp/getRoutingRules": ok(payload)}))
        self.assertEqual(["adhoc-header"], [r["name"] for r in gw.get_routing_rules()])

    def test_500_means_not_configured_not_broken(self):
        """With no routingRules block the Gateway NPEs into a 500. That is a
        configuration state, and the screen should hide the section rather than
        show an error."""
        gw = client(FakeTransport({"/webapp/getRoutingRules": HttpResponse(500, b"", 0.0)}))
        self.assertIsNone(gw.get_routing_rules())

    def test_endpoint_disappearing_does_not_raise(self):
        """It is undocumented; an upgrade may remove it. That must not take the
        rest of the screen down with it."""
        gw = client(FakeTransport({"/webapp/getRoutingRules": HttpResponse(404, b"", 0.0)}))
        self.assertIsNone(gw.get_routing_rules())

    def test_unreachable_gateway_yields_none(self):
        gw = client(FakeTransport({"/webapp/getRoutingRules": TransportError("refused")}))
        self.assertIsNone(gw.get_routing_rules())


class LivenessTest(unittest.TestCase):
    def test_live(self):
        gw = client(FakeTransport({"/trino-gateway/livez": HttpResponse(200, b"ok", 0.0)}))
        self.assertTrue(gw.is_live())

    def test_not_live(self):
        gw = client(FakeTransport({"/trino-gateway/livez": TransportError("refused")}))
        self.assertFalse(gw.is_live())

    def test_readyz_is_not_exposed(self):
        """It answers 200 with zero backends registered, so it cannot stand for
        "routing works". Offering it would invite exactly that reading."""
        self.assertFalse(hasattr(GatewayClient, "is_ready"))


class ActivationTest(unittest.TestCase):
    def test_deactivate_hits_the_right_path(self):
        transport = FakeTransport({"/gateway/backend/deactivate/prod-a":
                                   HttpResponse(200, b"", 0.0)})
        client(transport).set_active("prod-a", False)
        self.assertEqual("/gateway/backend/deactivate/prod-a", transport.calls[0]["path"])

    def test_activate_hits_the_right_path(self):
        transport = FakeTransport({"/gateway/backend/activate/prod-a":
                                   HttpResponse(200, b"", 0.0)})
        client(transport).set_active("prod-a", True)
        self.assertEqual("/gateway/backend/activate/prod-a", transport.calls[0]["path"])


class DeleteTest(unittest.TestCase):
    """The trap: 200 is returned for bodies the Gateway then ignores."""

    def test_delete_sends_a_plain_text_name(self):
        transport = FakeTransport({
            BACKEND_DELETE: HttpResponse(200, b"", 0.0),
            "/gateway/backend/all": ok([]),
        })
        client(transport).delete_backend("prod-a")
        sent = transport.calls[0]
        self.assertEqual(b"prod-a", sent["body"], "the endpoint takes plain text, not JSON")
        self.assertEqual("text/plain", sent["headers"].get("Content-Type"))

    def test_a_200_that_changed_nothing_is_reported_as_a_failure(self):
        """Measured: sending JSON returns 200 and deletes nothing. Reporting
        that as success tells an operator a cluster is gone while it is still
        routing queries."""
        transport = FakeTransport({
            BACKEND_DELETE: HttpResponse(200, b"", 0.0),
            "/gateway/backend/all": ok([BACKEND]),  # still there
        })
        with self.assertRaises(GatewayWriteNotApplied) as caught:
            client(transport).delete_backend("prod-a")
        self.assertIn("still registered", str(caught.exception))
        self.assertIn("plain-text", caught.exception.advice)

    def test_delete_verifies_by_reading_the_list(self):
        transport = FakeTransport({
            BACKEND_DELETE: HttpResponse(200, b"", 0.0),
            "/gateway/backend/all": ok([]),
        })
        client(transport).delete_backend("prod-a")
        self.assertIn("/gateway/backend/all",
                      [c["path"] for c in transport.calls],
                      "the status code alone proves nothing; the list must be re-read")


class BodyTest(unittest.TestCase):
    def test_add_sends_only_known_fields(self):
        transport = FakeTransport({"/gateway/backend/modify/add": ok(BACKEND)})
        client(transport).add_backend(dict(BACKEND, unexpected="drop me"))
        sent = json.loads(transport.calls[0]["body"].decode("utf-8"))
        self.assertNotIn("unexpected", sent)
        self.assertEqual(BACKEND, sent)


class CredentialTest(unittest.TestCase):
    def test_no_authorization_header_when_unconfigured(self):
        """Gateway may run with no authentication at all. An empty Basic header
        is worse than none - some stacks read it as a failed login."""
        transport = FakeTransport({"/gateway/backend/all": ok([])})
        client(transport).list_backends()
        self.assertNotIn("Authorization", transport.calls[0]["headers"])

    def test_authorization_header_when_configured(self):
        transport = FakeTransport({"/gateway/backend/all": ok([])})
        GatewayClient(base_url="http://gw:8090", user="tms-gateway", password="pw",
                      transport=transport).list_backends()
        self.assertTrue(
            transport.calls[0]["headers"]["Authorization"].startswith("Basic "))


if __name__ == "__main__":
    unittest.main()
