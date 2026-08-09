"""Trino Gateway client (FR-GATEWAY).

Same shape as TrinoClient - circuit breaker, error taxonomy, injected transport
- so failures degrade the same way and nothing new has to be learned to read it.

Four behaviours of Gateway 19 are encoded here because they were measured, not
read (TRINO_VERIFIED.md T2-3-1), and each of them will bite whoever forgets:

* `modify/delete` takes a **plain-text** name, not JSON, and answers 200 to
  anything. A 200 does not mean the backend is gone, so `delete_backend`
  re-reads the list and refuses to claim success it cannot see.
* `GET /webapp/getRoutingRules` exists but is undocumented, and 500s when the
  Gateway has no `routingRules` configured. It is treated as optional: absent
  rules are a normal state, not an error.
* `readyz` returns 200 with zero backends registered, so it says nothing about
  whether routing can actually happen. It is exposed as liveness only.
* The API role can write. There is no read-only role, so this client's
  credential is as sensitive as the Trino one - never log it, never echo it.

Python 3.9 compatible.
"""

import base64
import json
import logging
import time
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

log = logging.getLogger(__name__)

BACKENDS_ALL = "/gateway/backend/all"
BACKENDS_ACTIVE = "/gateway/backend/active"
BACKEND_ADD = "/gateway/backend/modify/add"
BACKEND_UPDATE = "/gateway/backend/modify/update"
BACKEND_DELETE = "/gateway/backend/modify/delete"
BACKEND_ACTIVATE = "/gateway/backend/activate/"
BACKEND_DEACTIVATE = "/gateway/backend/deactivate/"
ROUTING_RULES = "/webapp/getRoutingRules"
LIVEZ = "/trino-gateway/livez"

BACKEND_FIELDS = ("name", "proxyTo", "active", "routingGroup", "externalUrl")


class GatewayWriteNotApplied(TrinoClientError):
    """The Gateway answered 200 but the change is not visible.

    Specific to `modify/delete`, which returns 200 for payload shapes it then
    ignores. Reporting the 200 as success would tell an operator a cluster was
    removed while it is still routing queries.
    """

    advice = (
        "The Gateway accepted the request but the change is not in the backend "
        "list. For delete this usually means the body shape was wrong - it "
        "takes a plain-text name, not JSON. Re-check the backend list before "
        "assuming anything changed."
    )


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        user: str = "",
        password: str = "",
        transport: Optional[Transport] = None,
        verify_tls: bool = True,
        connect_timeout: float = 2.0,
        read_timeout: float = 5.0,
        write_timeout: float = 10.0,
        read_retries: int = 2,
        breaker: Optional[CircuitBreaker] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        if transport is None:  # pragma: no cover - production wiring
            from tms.clients.transport import HttpxTransport

            transport = HttpxTransport(verify_tls=verify_tls)
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

    def _headers(self, content_type: Optional[str] = None) -> Dict[str, str]:
        headers = {}
        # Gateway may be running with no authentication at all (measured). Send
        # credentials only when we have them rather than an empty Basic header,
        # which some stacks treat as a failed login.
        if self.user or self._password:
            token = base64.b64encode(
                "{}:{}".format(self.user, self._password).encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = "Basic " + token
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _call(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
        content_type: Optional[str] = None,
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
        headers = self._headers(content_type)
        attempts = retries + 1
        last_error = None  # type: Optional[TrinoClientError]
        for attempt in range(attempts):
            try:
                response = self._transport.request(
                    method, url, headers=headers, body=body,
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
                    self.breaker.record_failure(transient=False)
                    raise last_error
            if attempt < attempts - 1:
                self._sleep(0.2 * (2 ** attempt))

        self.breaker.record_failure(transient=True)
        raise last_error if last_error else TrinoUnavailable(path)

    @staticmethod
    def _json(response: HttpResponse, path: str) -> Any:
        try:
            return json.loads(response.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TrinoProtocolError("{}: invalid JSON ({})".format(path, exc)) from exc

    # ------------------------------------------------------------------ read

    def list_backends(self, active_only: bool = False) -> List[Dict[str, Any]]:
        path = BACKENDS_ACTIVE if active_only else BACKENDS_ALL
        payload = self._json(self._call("GET", path, retries=self.read_retries), path)
        if not isinstance(payload, list):
            raise TrinoProtocolError("{}: expected a list".format(path))
        return [b for b in payload if isinstance(b, dict)]

    def get_routing_rules(self) -> Optional[List[Dict[str, Any]]]:
        """Rules, or None when the Gateway has none configured.

        Undocumented endpoint. With no `routingRules` block the Gateway NPEs
        into a 500, which is a configuration state rather than a fault - the
        caller gets None and hides the section instead of showing an error.
        Any 5xx is treated the same way for the same reason: a Gateway upgrade
        that removes this endpoint must not break the whole screen.
        """
        try:
            response = self._call("GET", ROUTING_RULES, retries=self.read_retries)
        except TrinoClientError as exc:
            log.info("routing rules unavailable (%s): %s", type(exc).__name__, exc)
            return None
        payload = self._json(response, ROUTING_RULES)
        if isinstance(payload, dict):
            data = payload.get("data")
            return data if isinstance(data, list) else None
        return payload if isinstance(payload, list) else None

    def is_live(self) -> bool:
        """Liveness only.

        Deliberately not exposing readyz: it answers 200 with zero backends
        registered (measured), so it cannot be used to claim routing works.
        """
        try:
            self._call("GET", LIVEZ)
            return True
        except TrinoClientError:
            return False

    # ----------------------------------------------------------------- write

    def _backend_body(self, backend: Dict[str, Any]) -> bytes:
        payload = {k: backend.get(k) for k in BACKEND_FIELDS if k in backend}
        return json.dumps(payload).encode("utf-8")

    def set_active(self, name: str, active: bool) -> None:
        """Stop or resume new queries reaching a cluster.

        This is step 1 and step 5 of the destructive-action safe sequence
        (CLAUDE.md rule 5). It stops *new* queries only - running ones continue.
        """
        path = (BACKEND_ACTIVATE if active else BACKEND_DEACTIVATE) + name
        self._call("POST", path, timeout=self.write_timeout)

    def add_backend(self, backend: Dict[str, Any]) -> Dict[str, Any]:
        response = self._call("POST", BACKEND_ADD, body=self._backend_body(backend),
                              content_type="application/json",
                              timeout=self.write_timeout)
        return self._json(response, BACKEND_ADD)

    def update_backend(self, backend: Dict[str, Any]) -> Dict[str, Any]:
        response = self._call("POST", BACKEND_UPDATE, body=self._backend_body(backend),
                              content_type="application/json",
                              timeout=self.write_timeout)
        return self._json(response, BACKEND_UPDATE)

    def delete_backend(self, name: str) -> None:
        """Remove a backend, then prove it is gone.

        The endpoint takes a plain-text name and returns 200 for shapes it
        ignores, so the status code carries no information. Confirm against the
        list and raise if the backend is still there rather than reporting a
        deletion that did not happen.
        """
        self._call("POST", BACKEND_DELETE, body=name.encode("utf-8"),
                   content_type="text/plain", timeout=self.write_timeout)
        remaining = {b.get("name") for b in self.list_backends()}
        if name in remaining:
            raise GatewayWriteNotApplied(
                "Gateway returned 200 but backend '{}' is still registered".format(name)
            )

    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if close:
            close()


def build_gateway_client(config):
    """One client construction, shared by the collector and the API.

    Returns None when the Gateway is disabled, so callers can treat "no
    Gateway" as a first-class case instead of holding a client that would fail
    on first use. Both processes need one - the collector to poll backends, the
    API to deactivate a cluster during a restart - and building it twice is how
    the two drift apart.
    """
    if not config.gateway.enabled or not config.gateway.base_url:
        return None
    from tms.clients.transport import HttpxTransport

    return GatewayClient(
        base_url=config.gateway.base_url,
        user=config.gateway.user,
        password=config.gateway.password.reveal(),
        transport=HttpxTransport(verify_tls=config.trino.verify_tls),
        verify_tls=config.trino.verify_tls,
        connect_timeout=config.trino.connect_timeout_seconds,
        read_timeout=config.trino.read_timeout_seconds,
        write_timeout=config.trino.write_timeout_seconds,
        read_retries=config.trino.read_retries,
    )
