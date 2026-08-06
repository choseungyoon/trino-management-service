"""Trino coordinator client.

Every endpoint used here was verified against Trino 477 - documentation where it
exists, source at tag 477 where it does not, and a live cluster on 2026-08-06.
See docs/TRINO_VERIFIED.md. Do not add a call that is not recorded there.

Two rules this module exists to enforce:

* TMS never submits SQL. `system.runtime.*` would consume coordinator query
  slots and, worse, inject ~17,000 TMS queries a day into the separate
  query-history project's data (ARCHITECTURE.md principle A1).
* TMS never sends `X-Trino-User`. Keeping the authenticated user equal to the
  session user means `checkCanImpersonateUser` is never reached
  (TRINO_VERIFIED.md T3-5).

Python 3.9 compatible.
"""

import base64
import json
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

from tms.clients.circuit import CircuitBreaker
from tms.clients.errors import (
    CircuitOpen,
    TrinoClientError,
    TrinoProtocolError,
    TrinoUnavailable,
    classify_status,
)
from tms.clients.transport import HttpResponse, Transport, TransportError

# Non-terminal states, i.e. queries that are still in flight.
# Verified against io/trino/execution/QueryState.java @477.
# FINISHED and FAILED are deliberately excluded: completed queries belong to the
# separate query-history project (DECISIONS.md D-001) and asking for them
# inflates the response for no benefit.
LIVE_STATES = (
    "QUEUED",
    "WAITING_FOR_RESOURCES",
    "DISPATCHING",
    "PLANNING",
    "STARTING",
    "RUNNING",
    "FINISHING",
)

# MBean holding node counts on the coordinator. Verified live 2026-08-06.
# NOT the FailureDetector MBean the 477 docs still advertise - that module is
# not installed in 477 and the endpoint answers 500 (TRINO_VERIFIED.md T1-7).
NODE_MANAGER_MBEAN = "trino.node:name=CoordinatorNodeManager"


class QueryListResult:
    """Live queries plus the transfer size, which drives collector backoff."""

    __slots__ = ("queries", "response_bytes", "elapsed_seconds")

    def __init__(
        self, queries: List[Dict[str, Any]], response_bytes: int, elapsed_seconds: float
    ) -> None:
        self.queries = queries
        self.response_bytes = response_bytes
        self.elapsed_seconds = elapsed_seconds

    def __len__(self) -> int:
        return len(self.queries)


def build_kill_message(actor: str, reason: str, request_id: str) -> str:
    """Compose the body of PUT /v1/query/{id}/killed.

    Trino returns this text to the user whose query was killed, so the operator's
    reason reaches them directly (AUDIT_MODEL.md 4-2). Newlines are stripped and
    the reason is capped because this is surfaced as an error message.
    """
    cleaned = " ".join(reason.split())
    if len(cleaned) > 512:
        cleaned = cleaned[:509] + "..."
    return "Killed by TMS. actor={}, reason={}, request_id={}".format(
        actor, cleaned, request_id
    )


class TrinoClient:
    """Synchronous, single coordinator, read-mostly.

    Timeouts, retries and the circuit breaker follow ARCHITECTURE.md 4-1:
    reads retry, writes never do (a retried kill can kill twice).
    """

    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        transport: Transport,
        verify_tls: bool = True,
        connect_timeout: float = 2.0,
        read_timeout: float = 5.0,
        write_timeout: float = 10.0,
        read_retries: int = 2,
        breaker: Optional[CircuitBreaker] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user = user
        self._password = password
        self._transport = transport
        self.verify_tls = verify_tls
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout
        self.read_retries = read_retries
        self.breaker = breaker or CircuitBreaker()
        self._sleep = sleep or time.sleep

    # ------------------------------------------------------------------ core

    def _auth_header(self) -> Dict[str, str]:
        token = base64.b64encode(
            "{}:{}".format(self.user, self._password).encode("utf-8")
        ).decode("ascii")
        # No X-Trino-User: see module docstring.
        return {"Authorization": "Basic " + token}

    def _call(
        self,
        method: str,
        path: str,
        authenticated: bool = True,
        body: Optional[bytes] = None,
        retries: int = 0,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        if not self.breaker.allows_request():
            raise CircuitOpen(
                "circuit open for {} ({:.0f}s remaining)".format(
                    self.base_url, self.breaker.seconds_until_retry()
                )
            )

        url = self.base_url + path
        headers = self._auth_header() if authenticated else {}
        if body is not None:
            headers["Content-Type"] = "application/json"

        attempts = retries + 1
        last_error = None  # type: Optional[TrinoClientError]
        for attempt in range(attempts):
            try:
                response = self._transport.request(
                    method,
                    url,
                    headers=headers,
                    body=body,
                    connect_timeout=self.connect_timeout,
                    read_timeout=timeout if timeout is not None else self.read_timeout,
                    verify_tls=self.verify_tls,
                )
            except TransportError as exc:
                last_error = TrinoUnavailable("{}: {}".format(path, exc))
            else:
                if 200 <= response.status < 300:
                    self.breaker.record_success()
                    return response
                last_error = classify_status(response.status, path)
                if not last_error.transient:
                    # Permanent: report it as-is so the operator sees the fix.
                    self.breaker.record_failure(transient=False)
                    raise last_error

            if attempt < attempts - 1:
                self._sleep(0.2 * (2**attempt))  # 0.2s, 0.4s, ...

        self.breaker.record_failure(transient=True)
        raise last_error if last_error else TrinoUnavailable(path)

    @staticmethod
    def _json(response: HttpResponse, path: str) -> Any:
        try:
            return json.loads(response.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TrinoProtocolError("{}: invalid JSON ({})".format(path, exc)) from exc

    # --------------------------------------------------------------- public

    def get_server_info(self) -> Dict[str, Any]:
        """GET /v1/info - PUBLIC, no credentials required.

        This is the last thing to stop working: it needs neither authentication
        nor an access-control rule, so H-01/H-02 keep answering when OPA or
        rules.json block everything else (TRINO_VERIFIED.md T3-6).
        """
        response = self._call("GET", "/v1/info", authenticated=False, retries=self.read_retries)
        info = self._json(response, "/v1/info")
        if not isinstance(info, dict):
            raise TrinoProtocolError("/v1/info: expected an object")
        return info

    def get_node_state(self) -> str:
        """GET /v1/info/state - PUBLIC. Returns a bare JSON string."""
        response = self._call(
            "GET", "/v1/info/state", authenticated=False, retries=self.read_retries
        )
        state = self._json(response, "/v1/info/state")
        if not isinstance(state, str):
            raise TrinoProtocolError("/v1/info/state: expected a string")
        return state

    def list_mbean_names(self) -> List[str]:
        """GET /v1/jmx/mbean - the registry.

        Enumerating beats trusting documentation: the 477 docs list an MBean the
        release does not install.
        """
        response = self._call("GET", "/v1/jmx/mbean", retries=self.read_retries)
        payload = self._json(response, "/v1/jmx/mbean")
        if not isinstance(payload, list):
            raise TrinoProtocolError("/v1/jmx/mbean: expected a list")
        return [
            str(entry["objectName"])
            for entry in payload
            if isinstance(entry, dict) and entry.get("objectName")
        ]

    def get_mbean(self, object_name: str) -> Dict[str, Any]:
        """GET /v1/jmx/mbean/{objectName}, flattened to attribute -> value.

        Raises MBeanNotRegistered on 500, which is how a stale name presents
        itself (airlift maps no JMX exception).
        """
        path = "/v1/jmx/mbean/" + urllib.parse.quote(object_name, safe="")
        response = self._call("GET", path, retries=self.read_retries)
        payload = self._json(response, path)
        if not isinstance(payload, dict):
            raise TrinoProtocolError("{}: expected an object".format(path))
        attributes = {}  # type: Dict[str, Any]
        for attribute in payload.get("attributes") or []:
            if isinstance(attribute, dict) and attribute.get("name") is not None:
                attributes[str(attribute["name"])] = attribute.get("value")
        return attributes

    def get_node_counts(self) -> Dict[str, int]:
        """Node counts from CoordinatorNodeManager.

        Returns Active/Inactive/Draining/Drained/ShuttingDown. The split lets
        H-03 tell a failure apart from a planned drain, so that shrinking the
        fleet on purpose does not paint the health page red.
        """
        attributes = self.get_mbean(NODE_MANAGER_MBEAN)
        counts = {}  # type: Dict[str, int]
        for key, value in attributes.items():
            if key.endswith("NodeCount") and isinstance(value, (int, float)):
                counts[key] = int(value)
        if "ActiveNodeCount" not in counts:
            raise TrinoProtocolError(
                "{}: ActiveNodeCount missing".format(NODE_MANAGER_MBEAN)
            )
        return counts

    def list_queries(self, states: Optional[List[str]] = None) -> QueryListResult:
        """GET /v1/query?state=... - live queries only.

        Note the failure mode this cannot detect on its own: with `file` access
        control, a denied `queries` rule filters the list to empty rather than
        returning 403. An empty result is therefore ambiguous, and the caller
        must cross-check it against JMX RunningQueries (health test H-09).
        """
        selected = list(states) if states else list(LIVE_STATES)
        query = urllib.parse.urlencode([("state", state) for state in selected])
        path = "/v1/query?" + query
        response = self._call("GET", path, retries=self.read_retries)
        payload = self._json(response, path)
        if not isinstance(payload, list):
            raise TrinoProtocolError("/v1/query: expected a list")
        queries = [entry for entry in payload if isinstance(entry, dict)]
        return QueryListResult(queries, response.size_bytes, response.elapsed_seconds)

    def get_query(self, query_id: str) -> Dict[str, Any]:
        """GET /v1/query/{queryId} - full detail including complete SQL.

        Only called when a user opens a query, never on the polling path.
        """
        path = "/v1/query/" + urllib.parse.quote(query_id, safe="")
        response = self._call("GET", path, retries=self.read_retries)
        payload = self._json(response, path)
        if not isinstance(payload, dict):
            raise TrinoProtocolError("{}: expected an object".format(path))
        return payload

    def kill_query(self, query_id: str, message: str) -> None:
        """PUT /v1/query/{queryId}/killed with the reason as the body.

        Never retried: a retry can kill a second, unrelated query that happens
        to reuse the id, and the caller has already written an audit record.
        Chosen over DELETE /v1/query/{id} because only this endpoint carries a
        message back to the user whose query was killed.
        """
        path = "/v1/query/" + urllib.parse.quote(query_id, safe="") + "/killed"
        self._call(
            "PUT",
            path,
            body=message.encode("utf-8"),
            retries=0,
            timeout=self.write_timeout,
        )

    def close(self) -> None:
        self._transport.close()
