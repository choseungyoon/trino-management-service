"""Talking to an individual Trino node (FR-FL-01, FR-FL-03).

Separate from `clients/trino.py` because the target is different. That client
talks to a coordinator about the cluster; this one talks to *a node* about
itself, including workers, which have no query API at all.

Measured against Trino 477 on 2026-08-09:

| call                    | security          | measured |
|-------------------------|-------------------|----------|
| `GET /v1/info`          | PUBLIC            | 200 with no credentials at all |
| `GET /v1/info/state`    | PUBLIC            | 200, body is a JSON string |
| `PUT /v1/info/state`    | MANAGEMENT_WRITE  | 403 "Management only resource" for an account without `WriteSystemInformation` |

⛔ `GET /v1/node` does not exist in 477 - it 404s. REQUIREMENTS described it as
an unreliable secondary source; it is not a source at all. Per-node facts come
from each node's own `/v1/info`.

Reads send no credentials
-------------------------
`/v1/info` is PUBLIC, so sending the TMS password to every worker on every poll
would spread a management credential across the fleet for no benefit. Only the
shutdown write authenticates.

Python 3.9 compatible.
"""

import base64
import json
import logging
from typing import Any, Dict, Optional

from tms.clients.errors import TrinoClientError, TrinoForbidden

log = logging.getLogger(__name__)

SHUTTING_DOWN = "SHUTTING_DOWN"
ACTIVE = "ACTIVE"


class NodeUnreachable(TrinoClientError):
    """The node did not answer. Not the same as the node saying no."""


class NodeClient:
    """One node's `/v1/info` family.

    Timeouts are short: a fleet screen polls every node, and one unreachable
    host must not hold up the other eleven. Unreachable is a fact to display,
    not an error to raise at the screen.
    """

    def __init__(self, base_url: str, transport, user: str = "",
                 password: str = "", verify_tls: bool = True,
                 connect_timeout: float = 2.0, read_timeout: float = 4.0,
                 write_timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self.user = user
        self._password = password
        self.verify_tls = verify_tls
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout

    # ------------------------------------------------------------------ read

    def info(self) -> Dict[str, Any]:
        """`GET /v1/info` - the node describing itself.

        Returns nodeId, state, nodeVersion.version, environment, coordinator,
        coordinatorId, starting and uptime. Verified on 477.
        """
        response = self._get("/v1/info")
        payload = self._json(response, "/v1/info")
        if not isinstance(payload, dict):
            raise TrinoClientError("/v1/info did not return an object")
        return payload

    def state(self) -> str:
        """`GET /v1/info/state`. The body is a JSON string, quotes included."""
        response = self._get("/v1/info/state")
        value = self._json(response, "/v1/info/state")
        return str(value)

    # ----------------------------------------------------------------- write

    def begin_shutdown(self, reason_actor: str = "") -> None:
        """`PUT /v1/info/state` with `"SHUTTING_DOWN"` (FR-FL-03).

        ⛔ This is not "stop the process". Trino's own sequence is: enter
        SHUTTING_DOWN, wait one `shutdown.grace-period` so the coordinator stops
        sending work, block until active tasks finish, wait another grace
        period, then exit. A worker therefore takes at least
        `2 x shutdown.grace-period` plus its running tasks to go away - four
        minutes on the default settings. Anything that times out sooner than
        that is timing out on a healthy shutdown.

        The body is a JSON *string*: the quotes are part of the payload.
        """
        body = json.dumps(SHUTTING_DOWN).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.user:
            headers["X-Trino-User"] = self.user
            token = base64.b64encode(
                "{}:{}".format(self.user, self._password).encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = "Basic " + token
        try:
            response = self._transport.request(
                "PUT", self.base_url + "/v1/info/state", headers=headers, body=body,
                connect_timeout=self.connect_timeout,
                read_timeout=self.write_timeout,
                verify_tls=self.verify_tls,
            )
        except TrinoClientError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise NodeUnreachable(
                "{} did not answer the shutdown request: {}".format(
                    self.base_url, exc)) from exc
        if response.status == 403:
            # The single most likely failure, and the message Trino returns
            # ("Management only resource") does not say what to do about it.
            raise TrinoForbidden(
                "{} refused the shutdown: the TMS account has no "
                "'WriteSystemInformation' permission on this node. Graceful "
                "shutdown needs it on every worker, not just the coordinator "
                "(TRINO_VERIFIED.md T1-2, T3-4).".format(self.base_url))
        if response.status >= 400:
            raise TrinoClientError(
                "{} returned {} for the shutdown request".format(
                    self.base_url, response.status))

    # ----------------------------------------------------------------- inner

    def _get(self, path: str):
        try:
            # No credentials: these are PUBLIC, and posting a management
            # password to every worker on every poll spreads it for nothing.
            return self._transport.request(
                "GET", self.base_url + path, headers={}, body=None,
                connect_timeout=self.connect_timeout,
                read_timeout=self.read_timeout,
                verify_tls=self.verify_tls,
            )
        except TrinoClientError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise NodeUnreachable("{}{}: {}".format(self.base_url, path, exc)) from exc

    @staticmethod
    def _json(response, path: str) -> Any:
        if response.status >= 400:
            raise TrinoClientError("{} returned {}".format(path, response.status))
        try:
            return json.loads(response.text())
        except ValueError as exc:
            raise TrinoClientError("{} returned invalid JSON".format(path)) from exc

    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if close:
            close()
