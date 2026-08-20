"""The one place TMS submits SQL to Trino (D-012).

Principle A1 used to be "TMS does not submit SQL", and the mechanism enforcing
it was the absence of `execute` in the OPA `queries` rule. D-012 granted that
permission, so the principle narrowed rather than disappeared:

    TMS does not submit SQL **on the polling path**.

The mechanism moved from a permission to this module. SQL leaves TMS here and
nowhere else, and `tests/test_sql_isolation.py` fails if anything under
`tms.collector.*` imports it - because the cost A1 worried about was never SQL
itself, it was SQL *on a timer*:

* a query per poll consumes coordinator query and resource-group slots - the
  management tool eating the capacity it manages;
* roughly 17,000 TMS queries a day land in the separate query-history system,
  polluting exactly the dataset WORKLOAD_PROFILE.md needs;
* every one of them is another OPA authorization call.

None of that follows from being able to run a query. All of it follows from
running one every five seconds.

Python 3.9 compatible.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from tms.clients.errors import TrinoClientError, TrinoProtocolError

log = logging.getLogger(__name__)

STATEMENT_PATH = "/v1/statement"

#: A query TMS runs is a query someone is waiting on. Trino's client protocol
#: returns immediately with a `nextUri` and expects polling, so this is a
#: ceiling on the whole exchange rather than on one request.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Trino asks clients to pause between `nextUri` fetches. Short enough that a
#: fast query still feels immediate.
POLL_INTERVAL_SECONDS = 0.05


class QueryFailed(TrinoClientError):
    """Trino accepted the statement and then failed it.

    Distinct from a transport error: the cluster is reachable and answered, so
    retrying the same statement will fail the same way.
    """


class SqlClient:
    """Submits one statement and collects its rows.

    Deliberately minimal - no cursors, no streaming, no parameter binding. The
    statements TMS runs are fixed strings against `system.runtime`, and every
    feature this class does not have is a feature that cannot be misused from
    a request.
    """

    def __init__(self, trino_client, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                 sleep=time.sleep) -> None:
        # Reuses the configured TrinoClient for auth, TLS and the circuit
        # breaker rather than opening a second, differently-configured path to
        # the same coordinator.
        self._client = trino_client
        self._timeout = timeout_seconds
        self._sleep = sleep

    def query(self, statement: str) -> List[Dict[str, Any]]:
        """Run `statement`, follow `nextUri` to the end, return rows as dicts.

        Raises `QueryFailed` if Trino reports an error, `TrinoClientError` if
        the exchange does not complete in time. Never returns partial results:
        a caller that got half a node list would draw the wrong conclusion from
        it, which is worse than being told the answer is unavailable.
        """
        deadline = time.monotonic() + self._timeout
        payload = self._post(statement)

        columns: List[str] = []
        rows: List[List[Any]] = []
        while True:
            if payload.get("error"):
                raise QueryFailed(_error_text(payload["error"]))
            columns = columns or [c.get("name") for c in payload.get("columns") or []]
            rows.extend(payload.get("data") or [])

            next_uri = payload.get("nextUri")
            if not next_uri:
                break
            if time.monotonic() > deadline:
                # Abandoning the exchange leaves the query running on the
                # coordinator, so say so rather than implying it was stopped.
                raise TrinoClientError(
                    "the query did not finish within {:g}s and TMS stopped "
                    "waiting; it may still be running on the "
                    "coordinator".format(self._timeout))
            self._sleep(POLL_INTERVAL_SECONDS)
            payload = self._get(next_uri)

        return [dict(zip(columns, row)) for row in rows]

    # ------------------------------------------------------------- transport

    def _post(self, statement: str) -> Dict[str, Any]:
        # retries=0 on purpose. A retried statement is a second statement:
        # harmless for the SELECTs TMS runs today, and exactly the assumption
        # that would quietly stop holding the first time someone adds one that
        # is not a SELECT.
        response = self._client._call(  # noqa: SLF001 - same package, one client
            "POST", STATEMENT_PATH, body=statement.encode("utf-8"), retries=0)
        return self._payload(response, STATEMENT_PATH)

    def _get(self, next_uri: str) -> Dict[str, Any]:
        # `nextUri` is absolute and Trino's own. Reduced to a path so the
        # request goes to the coordinator TMS is configured for, never to
        # whatever host a response happened to name.
        path = _path_of(next_uri)
        response = self._client._call("GET", path, retries=0)  # noqa: SLF001
        return self._payload(response, path)

    @staticmethod
    def _payload(response, path: str) -> Dict[str, Any]:
        try:
            payload = json.loads(response.text or "{}")
        except ValueError as exc:
            raise TrinoProtocolError("{}: not JSON ({})".format(path, exc))
        if not isinstance(payload, dict):
            raise TrinoProtocolError("{}: expected an object".format(path))
        return payload


def _path_of(uri: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(uri)
    return parts.path + (("?" + parts.query) if parts.query else "")


def _error_text(error: Dict[str, Any]) -> str:
    name = error.get("errorName") or error.get("errorCode") or "error"
    message = error.get("message") or ""
    return "{}: {}".format(name, message).strip(": ")
