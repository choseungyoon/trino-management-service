"""Submitting one statement and collecting its rows (D-012).

Trino's client protocol answers immediately and expects the caller to follow
`nextUri` until it stops appearing, so most of what can go wrong here is in the
following rather than in the asking.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from tms.clients.errors import TrinoClientError  # noqa: E402
from tms.clients.sql import QueryFailed, SqlClient, _path_of  # noqa: E402


class Response:
    def __init__(self, text):
        self.text = text
        self.status_code = 200


class StubTrino:
    """Answers the statement protocol from a scripted list of payloads."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def _call(self, method, path, retries=0, body=None, **kwargs):
        self.calls.append((method, path, body))
        import json

        return Response(json.dumps(self.payloads.pop(0)))


def client(payloads):
    return SqlClient(StubTrino(payloads), sleep=lambda _s: None)


NODE_COLUMNS = [{"name": "node_id"}, {"name": "http_uri"}, {"name": "state"}]


class QueryTest(unittest.TestCase):
    def test_rows_come_back_keyed_by_column(self):
        rows = client([{
            "columns": NODE_COLUMNS,
            "data": [["w1", "https://w1:8443", "active"]],
        }]).query("SELECT * FROM system.runtime.nodes")
        self.assertEqual(
            [{"node_id": "w1", "http_uri": "https://w1:8443", "state": "active"}], rows)

    def test_pages_are_followed_and_concatenated(self):
        """Trino splits results across `nextUri` fetches, and a caller that
        stopped at the first page would report half a cluster."""
        rows = client([
            {"columns": NODE_COLUMNS, "data": [["w1", "u1", "active"]],
             "nextUri": "https://c:8443/v1/statement/x/2"},
            {"data": [["w2", "u2", "active"]],
             "nextUri": "https://c:8443/v1/statement/x/3"},
            {"data": [["w3", "u3", "active"]]},
        ]).query("SELECT 1")
        self.assertEqual(["w1", "w2", "w3"], [r["node_id"] for r in rows])

    def test_a_first_page_with_no_rows_is_not_the_end(self):
        """Trino routinely answers the POST with columns and no data yet."""
        rows = client([
            {"columns": NODE_COLUMNS, "nextUri": "https://c:8443/v1/statement/x/2"},
            {"data": [["w1", "u1", "active"]]},
        ]).query("SELECT 1")
        self.assertEqual(1, len(rows))

    def test_an_error_payload_is_raised_with_trinos_own_message(self):
        with self.assertRaises(QueryFailed) as caught:
            client([{"error": {"errorName": "PERMISSION_DENIED",
                               "message": "Cannot execute query"}}]).query("SELECT 1")
        self.assertIn("PERMISSION_DENIED", str(caught.exception))
        self.assertIn("Cannot execute query", str(caught.exception))

    def test_an_error_on_a_later_page_still_raises(self):
        """A statement can fail after it has already returned rows, and a
        partial answer is worse than no answer."""
        with self.assertRaises(QueryFailed):
            client([
                {"columns": NODE_COLUMNS, "data": [["w1", "u1", "active"]],
                 "nextUri": "https://c:8443/v1/statement/x/2"},
                {"error": {"errorName": "EXCEEDED_TIME_LIMIT", "message": "too slow"}},
            ]).query("SELECT 1")

    def test_running_out_of_time_says_the_query_may_still_be_running(self):
        """TMS stops waiting; the coordinator does not stop working."""
        stub = StubTrino([
            {"columns": NODE_COLUMNS, "nextUri": "https://c:8443/v1/statement/x/2"},
        ] * 3)
        sql = SqlClient(stub, timeout_seconds=-1, sleep=lambda _s: None)
        with self.assertRaises(TrinoClientError) as caught:
            sql.query("SELECT 1")
        self.assertIn("may still be running", str(caught.exception))

    def test_the_statement_is_posted_as_the_body(self):
        stub = StubTrino([{"columns": NODE_COLUMNS, "data": []}])
        SqlClient(stub, sleep=lambda _s: None).query("SELECT 42")
        method, path, body = stub.calls[0]
        self.assertEqual(("POST", "/v1/statement"), (method, path))
        self.assertEqual(b"SELECT 42", body)


class NextUriTest(unittest.TestCase):
    def test_only_the_path_is_taken_from_next_uri(self):
        """`nextUri` is absolute. Following it verbatim would send TMS's
        credentials to whatever host a response named, rather than to the
        coordinator it is configured for."""
        self.assertEqual("/v1/statement/x/2",
                         _path_of("https://elsewhere.invalid:8443/v1/statement/x/2"))

    def test_the_query_string_survives(self):
        self.assertEqual("/v1/statement/x/2?slug=abc",
                         _path_of("https://c:8443/v1/statement/x/2?slug=abc"))
